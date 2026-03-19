"""
Badge offset calibration dialog.

Flow:
1. User loads a screenshot (or the app captures one).
2. Screenshot is shown scaled inside the dialog.
3. Three steps guided by hint bar:
   Step 1 — Draw rectangle around the BADGE of any one player row.
   Step 2 — Draw rectangle around the NAME of that same player.
   Step 3 — Draw rectangle around the TOTAL SCORE of that same player.
4. App computes (dx, dy, w, h) for name and score relative to badge top-left.
5. "Preview" button: runs badge_detector on current screenshot and overlays
   all found badges + computed name/score zones so user can verify.
6. "Save" persists to settings.json via save_badge_offsets().
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

from PyQt6.QtCore import Qt, QRect, QPoint, QBuffer, QIODevice, QTimer, pyqtSignal, QProcess
from PyQt6.QtGui import (
    QPixmap, QPainter, QPen, QColor, QFont, QBrush, QImage,
    QKeySequence, QShortcut
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QSizePolicy, QFileDialog, QApplication
)
from ui.style_utils import mb_warning, mb_info
from PIL import Image

from config.settings_manager import save_badge_offsets, get_badge_offsets


# ------------------------------------------------------------------ #
#  Step definitions                                                    #
# ------------------------------------------------------------------ #

STEPS = [
    ("badge", QColor(255, 200,  50), "ШАГ 1/3 — Обведите БЕЙДЖ одного игрока"),
    ("name",  QColor( 80, 200, 120), "ШАГ 2/3 — Обведите зону ИМЕНИ этого же игрока"),
    ("score", QColor( 80, 160, 255), "ШАГ 3/3 — Обведите зону TOTAL SCORE этого же игрока"),
]

STYLESHEET = """
QDialog {
    background: #0a0e17;
    color: #d0ddf0;
    font-family: 'Segoe UI', sans-serif;
}
QLabel { color: #d0ddf0; font-size: 13px; }
QLabel#title {
    font-size: 18px; font-weight: bold;
    color: #f0c040; padding: 8px 0;
}
QLabel#hint {
    font-size: 13px; padding: 6px 12px;
    border-radius: 6px; background: #1a2035;
    border-left: 3px solid #f0c040;
}
QPushButton {
    background: #1e2840; color: #c8d8f0;
    border: 1px solid #2a3a5a; border-radius: 6px;
    padding: 7px 18px; font-size: 13px;
}
QPushButton:hover  { background: #2a3a5a; }
QPushButton:pressed{ background: #384870; }
QPushButton#primary {
    background: #1a4a7a; border-color: #3a7abf;
    color: #ffffff; font-weight: bold;
}
QPushButton#primary:hover { background: #2a5a9a; }
QPushButton#preview_btn {
    background: #1a3a2a; border-color: #2a6a4a;
    color: #80e0a0;
}
QPushButton#preview_btn:hover { background: #204a30; }
"""


# ------------------------------------------------------------------ #
#  Canvas                                                              #
# ------------------------------------------------------------------ #

class _CalibCanvas(QWidget):
    rect_drawn = pyqtSignal(str, QRect)

    def __init__(self, pixmap: QPixmap, orig_w: int, orig_h: int, parent=None):
        super().__init__(parent)
        self.pixmap   = pixmap
        self.orig_w   = orig_w
        self.orig_h   = orig_h
        self.scale    = 1.0
        self._off_x   = 0
        self._off_y   = 0

        self.current_step    = 0
        self.finished_rects: dict[str, QRect] = {}
        # Preview overlays: list of (bx, by, bw, bh) in original coords
        self.preview_badges: list[tuple[int, int, int, int]] = []
        self.preview_offsets: dict = {}

        self._start: QPoint | None = None
        self._cur_rect: QRect | None = None

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(800, 450)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---- painting -------------------------------------------------- #

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        wr = self.rect()
        scaled = self.pixmap.scaled(
            wr.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._off_x = (wr.width()  - scaled.width())  // 2
        self._off_y = (wr.height() - scaled.height()) // 2
        self.scale  = scaled.width() / self.orig_w
        painter.drawPixmap(self._off_x, self._off_y, scaled)

        # Confirmed step rects
        for name, rect in self.finished_rects.items():
            color = next(c for n, c, _ in STEPS if n == name)
            self._draw_rect(painter, rect, color, name.upper())

        # In-progress rubber band
        if self._cur_rect and self.current_step < len(STEPS):
            _, color, _ = STEPS[self.current_step]
            self._draw_rect(painter, self._cur_rect, color, dash=True)

        # Preview: detected badges + offset zones
        if self.preview_badges and self.preview_offsets:
            badge_color = QColor(255, 80, 80)
            name_color  = QColor(80, 200, 120)
            score_color = QColor(80, 160, 255)
            n_off = self.preview_offsets.get("name",  {})
            s_off = self.preview_offsets.get("score", {})
            for bx, by, bw, bh in self.preview_badges:
                self._draw_rect(painter, QRect(bx, by, bw, bh), badge_color, "BADGE")
                if n_off:
                    self._draw_rect(painter,
                        QRect(bx + n_off["dx"], by + n_off["dy"], n_off["w"], n_off["h"]),
                        name_color, "NAME")
                if s_off:
                    self._draw_rect(painter,
                        QRect(bx + s_off["dx"], by + s_off["dy"], s_off["w"], s_off["h"]),
                        score_color, "SCORE")

    def _draw_rect(self, painter: QPainter, rect: QRect, color: QColor,
                   label: str = "", dash: bool = False):
        sr = self._scale_rect(rect)
        style = Qt.PenStyle.DashLine if dash else Qt.PenStyle.SolidLine
        painter.setPen(QPen(color, 2, style))
        painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 35)))
        painter.drawRect(sr)
        if label:
            painter.setPen(QPen(color, 1))
            painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            painter.drawText(sr.topLeft() + QPoint(3, 12), label)

    def _scale_rect(self, r: QRect) -> QRect:
        return QRect(
            int(r.x() * self.scale) + self._off_x,
            int(r.y() * self.scale) + self._off_y,
            int(r.width()  * self.scale),
            int(r.height() * self.scale),
        )

    def _to_orig(self, p: QPoint) -> QPoint:
        return QPoint(
            int((p.x() - self._off_x) / self.scale),
            int((p.y() - self._off_y) / self.scale),
        )

    # ---- mouse ----------------------------------------------------- #

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.current_step < len(STEPS):
            self._start = self._to_orig(event.position().toPoint())

    def mouseMoveEvent(self, event):
        if self._start:
            end = self._to_orig(event.position().toPoint())
            self._cur_rect = QRect(self._start, end).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._start:
            end = self._to_orig(event.position().toPoint())
            rect = QRect(self._start, end).normalized()
            self._cur_rect = None
            self._start    = None
            name, _, _ = STEPS[self.current_step]
            self.finished_rects[name] = rect
            self.current_step += 1
            self.update()
            self.rect_drawn.emit(name, rect)


# ------------------------------------------------------------------ #
#  Dialog                                                              #
# ------------------------------------------------------------------ #

class BadgeOffsetCalibrationDialog(QDialog):
    calibration_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Калибровка смещений бейджа — Guild Tracker")
        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(1000, 700)

        self._rects: dict[str, QRect] = {}
        self._pil_img: Image.Image | None = None
        self.canvas: _CalibCanvas | None = None

        self._build_ui()
        QShortcut(QKeySequence("Ctrl+V"), self).activated.connect(self._paste_clipboard)

    # ---------------------------------------------------------------- #
    #  UI                                                               #
    # ---------------------------------------------------------------- #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Калибровка смещений (Badge → Name / Score)")
        title.setObjectName("title")
        layout.addWidget(title)

        desc = QLabel(
            "Обведите бейдж, имя и total score одного игрока. "
            "Приложение вычислит смещения и будет находить данные для всех бейджей автоматически."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #8090a0; font-size: 12px;")
        layout.addWidget(desc)

        self.hint_label = QLabel("Загрузите скриншот для начала калибровки.")
        self.hint_label.setObjectName("hint")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        # Canvas placeholder
        self.canvas_placeholder = QLabel("Нет изображения — используйте кнопки ниже")
        self.canvas_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas_placeholder.setStyleSheet("background:#141920; border-radius:8px; color:#3a5a7a;")
        self.canvas_placeholder.setMinimumHeight(400)
        layout.addWidget(self.canvas_placeholder, stretch=1)

        # Buttons
        btn_row = QHBoxLayout()

        load_btn = QPushButton("📂")
        load_btn.clicked.connect(self._load_file)

        capture_btn = QPushButton("✂ Захват")
        capture_btn.clicked.connect(self._open_snip)

        self.reset_btn = QPushButton("↩  Сбросить зоны")
        self.reset_btn.clicked.connect(self._reset)

        self.preview_btn = QPushButton("🔍  Предпросмотр")
        self.preview_btn.setObjectName("preview_btn")
        self.preview_btn.clicked.connect(self._preview)
        self.preview_btn.setEnabled(False)

        self.save_btn = QPushButton("✔  Сохранить калибровку")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)

        btn_row.addWidget(load_btn)
        btn_row.addWidget(capture_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.preview_btn)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

    # ---------------------------------------------------------------- #
    #  Screenshot loading                                               #
    # ---------------------------------------------------------------- #

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите скриншот", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self._set_image(Image.open(path).convert("RGB"))

    def _paste_clipboard(self):
        clipboard = QApplication.clipboard()
        qimg = clipboard.image()
        if qimg.isNull():
            self.hint_label.setText("⚠ Буфер обмена не содержит изображения.")
            return
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        qimg.save(buf, "PNG")
        buf.close()
        pil = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")
        self._set_image(pil)

    def _open_snip(self):
        print("[DEBUG] Badge calib: _open_snip() called", flush=True)
        # Не скрываем диалог - пусть overlay появится на top
        print("[DEBUG] Badge calib: About to call _do_snip() directly", flush=True)
        self._do_snip()

    def _restore_windows(self):
        for w in getattr(self, "_hidden_windows", []):
            try:
                w.show()
            except RuntimeError:
                pass
        self._hidden_windows = []

    def _do_snip(self):
        try:
            print("[DEBUG] Badge calib: _do_snip() called", flush=True)
            # Hide all visible app windows so the overlay has a clean desktop
            self._hidden_windows = [
                w for w in QApplication.topLevelWidgets() if w.isVisible()
            ]
            for w in self._hidden_windows:
                w.hide()
            QApplication.processEvents()  # let the OS repaint before screenshot

            self._snip_tmp = tempfile.mktemp(suffix=".png")
            script = os.path.join(os.path.dirname(__file__), "snip_proc.py")
            print(f"[DEBUG] Badge calib: Script path: {script}", flush=True)
            # Create QProcess WITHOUT parent to prevent destruction
            self._snip_proc = QProcess()
            # Set working directory to project root
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self._snip_proc.setWorkingDirectory(project_root)
            self._snip_proc.finished.connect(self._snip_finished)
            self._snip_proc.errorOccurred.connect(self._snip_error)
            self.hint_label.setText(f"🔧 Запуск snipping tool...")
            print(f"[DEBUG] Badge calib: Starting process: {sys.executable} {script} {self._snip_tmp}", flush=True)
            self._snip_proc.start(sys.executable, [script, self._snip_tmp])
            if not self._snip_proc.waitForStarted(3000):
                self.show()
                self.hint_label.setText("⚠ Не удалось запустить snipping tool")
        except Exception as e:
            print(f"[ERROR] Badge calib: Exception in _do_snip(): {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            self.show()
            self.hint_label.setText(f"⚠ Внутренняя ошибка: {e}")

    def _snip_finished(self, exit_code: int, _exit_status):
        if exit_code == 0 and os.path.exists(self._snip_tmp):
            try:
                pil = Image.open(self._snip_tmp).copy()
                self._on_snipped(pil)
            except Exception as e:
                self._restore_windows()
                self.hint_label.setText(f"⚠ Ошибка чтения снимка: {e}")
            finally:
                try:
                    os.unlink(self._snip_tmp)
                except OSError:
                    pass
        else:
            self._restore_windows()
            if exit_code != 0:
                stderr = self._snip_proc.readAllStandardError().data().decode('utf-8', errors='ignore')
                self.hint_label.setText(f"⚠ Процесс вернул ошибку: {stderr if stderr else 'неизвестная ошибка'}")
            try:
                os.unlink(self._snip_tmp)
            except OSError:
                pass

    def _snip_error(self, error):
        self._restore_windows()
        error_msg = self._snip_proc.errorString()
        self.hint_label.setText(f"⚠ Ошибка процесса: {error_msg}")

    def _on_snipped(self, pil_crop: Image.Image):
        self.show()
        self._set_image(pil_crop)

    def _set_image(self, pil_img: Image.Image):
        self._pil_img = pil_img
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        qimg   = QImage.fromData(buf.read())
        pixmap = QPixmap.fromImage(qimg)

        layout = self.layout()

        # Remove old canvas if present
        if self.canvas:
            layout.removeWidget(self.canvas)
            self.canvas.deleteLater()
            self.canvas = None

        idx = layout.indexOf(self.canvas_placeholder)
        layout.removeWidget(self.canvas_placeholder)
        self.canvas_placeholder.hide()

        self.canvas = _CalibCanvas(pixmap, pil_img.width, pil_img.height, self)
        self.canvas.rect_drawn.connect(self._on_rect_drawn)
        layout.insertWidget(idx, self.canvas, stretch=1)

        self._reset()

    # ---------------------------------------------------------------- #
    #  Step handling                                                     #
    # ---------------------------------------------------------------- #

    def _on_rect_drawn(self, name: str, rect: QRect):
        self._rects[name] = rect
        self._update_hint()
        all_done = len(self._rects) >= 3
        self.preview_btn.setEnabled(all_done and self._pil_img is not None)

    def _reset(self):
        self._rects = {}
        if self.canvas:
            self.canvas.finished_rects  = {}
            self.canvas.preview_badges  = []
            self.canvas.preview_offsets = {}
            self.canvas.current_step    = 0
            self.canvas.update()
        self.preview_btn.setEnabled(False)
        self._update_hint()

    def _update_hint(self):
        if not self.canvas:
            self.hint_label.setText("Загрузите скриншот для начала калибровки.")
            return
        step = self.canvas.current_step
        if step < len(STEPS):
            _, color, text = STEPS[step]
            hex_col = color.name()
            self.hint_label.setText(
                f'<span style="color:{hex_col}; font-weight:bold;">Шаг {step+1}/3 — </span>{text}'
            )
        else:
            self.hint_label.setText(
                "✅ Все зоны отмечены! Нажмите <b>Предпросмотр</b> для проверки или <b>Сохранить</b>."
            )

    # ---------------------------------------------------------------- #
    #  Preview                                                           #
    # ---------------------------------------------------------------- #

    def _preview(self):
        if not self._pil_img or len(self._rects) < 3:
            return

        offsets = self._compute_offsets()
        if offsets is None:
            return

        from core.badge_detector import load_templates, find_badges
        templates = load_templates()
        if not templates:
            mb_warning(self, "Нет шаблонов",
                "Шаблоны не найдены в assets/badges/.\n"
                "Убедитесь, что файлы B1Gold,B2Silver,B3Bronze,B4Default существуют.")
            return

        badges = find_badges(self._pil_img, templates,
                             threshold=0.75, offsets=offsets)
        self.canvas.preview_badges  = badges
        self.canvas.preview_offsets = offsets
        self.canvas.update()

        self.hint_label.setText(
            f"🔍 Предпросмотр: найдено <b>{len(badges)}</b> бейджей. "
            "Красные = бейдж, зелёные = имя, синие = score."
        )

    # ---------------------------------------------------------------- #
    #  Save                                                              #
    # ---------------------------------------------------------------- #

    def _compute_offsets(self) -> dict | None:
        if len(self._rects) < 3:
            mb_warning(self, "Не готово", "Обведите все три зоны.")
            return None

        badge_rect = self._rects["badge"]
        name_rect  = self._rects["name"]
        score_rect = self._rects["score"]

        return {
            "name": {
                "dx": name_rect.x()  - badge_rect.x(),
                "dy": name_rect.y()  - badge_rect.y(),
                "w":  name_rect.width(),
                "h":  name_rect.height(),
            },
            "score": {
                "dx": score_rect.x()  - badge_rect.x(),
                "dy": score_rect.y()  - badge_rect.y(),
                "w":  score_rect.width(),
                "h":  score_rect.height(),
            },
        }

    def _save(self):
        offsets = self._compute_offsets()
        if offsets is None:
            return
        save_badge_offsets(offsets)
        mb_info(
            self, "Сохранено",
            f"Калибровка сохранена!\n\n"
            f"Имя:   dx={offsets['name']['dx']}  dy={offsets['name']['dy']}  "
            f"{offsets['name']['w']}×{offsets['name']['h']} px\n"
            f"Score: dx={offsets['score']['dx']}  dy={offsets['score']['dy']}  "
            f"{offsets['score']['w']}×{offsets['score']['h']} px"
        )
        self.calibration_saved.emit()
        self.accept()
