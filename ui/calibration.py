"""
Calibration window.

Flow:
1. User opens Calibration window.
2. App takes a screenshot of the current screen.
3. Screenshot is shown scaled to fit inside the window.
4. User draws ONE rectangle around a single player row
   (spanning badge + name + score).
5. User marks the badge zone, name zone, and score zone
   within that row by drawing 3 sub-rectangles (guided step-by-step).
6. App asks how many rows are visible and measures the row height.
7. Normalised ROI is saved to settings.json for the current resolution.
"""

from __future__ import annotations

import pyautogui
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QPainter, QPen, QColor, QFont, QBrush, QImage
)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QWidget, QSizePolicy
)
from PIL import Image
import io
from ui.style_utils import mb_warning, mb_info

from config.settings_manager import save_roi_for_resolution


# ------------------------------------------------------------------ #
#  Canvas widget                                                       #
# ------------------------------------------------------------------ #

STEPS = [
    ("badge",   QColor(255, 200,  50), "ШАГ 1/4 — Обведите зону БЕЙДЖИКА (номер) для ПЕРВОЙ строки"),
    ("name",    QColor( 80, 200, 120), "ШАГ 2/4 — Обведите зону ИМЕНИ игрока для ПЕРВОЙ строки"),
    ("score",   QColor( 80, 160, 255), "ШАГ 3/4 — Обведите зону TOTAL SCORE для ПЕРВОЙ строки"),
    ("badge2",  QColor(255, 140,  30), "ШАГ 4/4 — Обведите зону БЕЙДЖИКА для ВТОРОЙ строки (нужно для шага скролла)"),
]


class CalibrationCanvas(QWidget):
    rect_drawn = pyqtSignal(str, QRect)  # zone_name, rect in ORIGINAL coords

    def __init__(self, pixmap: QPixmap, orig_w: int, orig_h: int, parent=None):
        super().__init__(parent)
        self.pixmap = pixmap
        self.orig_w = orig_w
        self.orig_h = orig_h
        self.scale = 1.0

        self.current_step = 0         # index into STEPS
        self.finished_rects: dict[str, QRect] = {}

        self._start: QPoint | None = None
        self._current_rect: QRect | None = None

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(800, 450)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---- drawing --------------------------------------------------- #

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw screenshot scaled to widget
        widget_rect = self.rect()
        scaled = self.pixmap.scaled(
            widget_rect.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        offset_x = (widget_rect.width()  - scaled.width())  // 2
        offset_y = (widget_rect.height() - scaled.height()) // 2
        painter.drawPixmap(offset_x, offset_y, scaled)

        self.scale     = scaled.width() / self.orig_w
        self._offset_x = offset_x
        self._offset_y = offset_y

        # Draw already-confirmed rects
        for name, rect in self.finished_rects.items():
            color = next(c for n, c, _ in STEPS if n == name)
            painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine))
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 40)))
            sr = self._scale_rect(rect)
            painter.drawRect(sr)
            painter.setPen(QPen(color, 1))
            painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            painter.drawText(sr.topLeft() + QPoint(3, 12), name.upper())

        # Draw in-progress rect
        if self._current_rect:
            _, color, _ = STEPS[self.current_step]
            painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 30)))
            painter.drawRect(self._scale_rect(self._current_rect))

    def _scale_rect(self, r: QRect) -> QRect:
        x = int(r.x() * self.scale) + self._offset_x
        y = int(r.y() * self.scale) + self._offset_y
        w = int(r.width()  * self.scale)
        h = int(r.height() * self.scale)
        return QRect(x, y, w, h)

    def _to_orig(self, p: QPoint) -> QPoint:
        return QPoint(
            int((p.x() - self._offset_x) / self.scale),
            int((p.y() - self._offset_y) / self.scale),
        )

    # ---- mouse ----------------------------------------------------- #

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.current_step < len(STEPS):
            self._start = self._to_orig(event.position().toPoint())

    def mouseMoveEvent(self, event):
        if self._start:
            end = self._to_orig(event.position().toPoint())
            self._current_rect = QRect(self._start, end).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._start:
            end = self._to_orig(event.position().toPoint())
            rect = QRect(self._start, end).normalized()
            self._current_rect = None
            self._start = None

            name, _, _ = STEPS[self.current_step]
            self.finished_rects[name] = rect
            self.current_step += 1
            self.update()
            self.rect_drawn.emit(name, rect)


# ------------------------------------------------------------------ #
#  Dialog                                                              #
# ------------------------------------------------------------------ #

STYLESHEET = """
QDialog {
    background: #0a0e17;
    color: #d0ddf0;
    font-family: 'Segoe UI', sans-serif;
}
QLabel {
    color: #d0ddf0;
    font-size: 13px;
}
QLabel#title {
    font-size: 18px;
    font-weight: bold;
    color: #f0c040;
    padding: 8px 0;
}
QLabel#hint {
    font-size: 13px;
    padding: 6px 12px;
    border-radius: 6px;
    background: #1a2035;
    border-left: 3px solid #f0c040;
}
QPushButton {
    background: #1e2840;
    color: #c8d8f0;
    border: 1px solid #2a3a5a;
    border-radius: 6px;
    padding: 7px 18px;
    font-size: 13px;
}
QPushButton:hover  { background: #2a3a5a; }
QPushButton:pressed{ background: #384870; }
QPushButton#primary {
    background: #1a4a7a;
    border-color: #3a7abf;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#primary:hover { background: #2a5a9a; }
QSpinBox {
    background: #1a2035;
    color: #d0ddf0;
    border: 1px solid #2a3a5a;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
}
QSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border-left: 1px solid #2a3a5a;
    border-bottom: 1px solid #2a3a5a; border-top-right-radius: 4px;
    background: #1e2840;
}
QSpinBox::up-button:hover { background: #2a3a5a; }
QSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border-left: 1px solid #2a3a5a;
    border-top: 1px solid #2a3a5a; border-bottom-right-radius: 4px;
    background: #1e2840;
}
QSpinBox::down-button:hover { background: #2a3a5a; }
QSpinBox::up-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid #8aabcf;
}
QSpinBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #8aabcf;
}
"""


class CalibrationDialog(QDialog):
    calibration_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Калибровка зон — Guild Tracker")
        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(1000, 680)

        self._rects: dict[str, QRect] = {}
        self._screen_w, self._screen_h = pyautogui.size()

        self._build_ui()
        self._take_screenshot()

    # ---------------------------------------------------------------- #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Калибровка зон распознавания")
        title.setObjectName("title")
        layout.addWidget(title)

        self.hint_label = QLabel()
        self.hint_label.setObjectName("hint")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        # Canvas placeholder — filled after screenshot
        self.canvas_placeholder = QLabel("Делаю скриншот…")
        self.canvas_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas_placeholder.setStyleSheet("background:#141920; border-radius:8px;")
        self.canvas_placeholder.setMinimumHeight(400)
        layout.addWidget(self.canvas_placeholder, stretch=1)

        # Row count
        row_count_row = QHBoxLayout()
        row_count_row.addWidget(QLabel("Видимых строк игроков на экране:"))
        self.row_count_spin = QSpinBox()
        self.row_count_spin.setRange(1, 20)
        self.row_count_spin.setValue(5)
        row_count_row.addWidget(self.row_count_spin)
        row_count_row.addStretch()
        layout.addLayout(row_count_row)

        # Buttons
        btn_row = QHBoxLayout()
        self.retake_btn = QPushButton("🔄  Новый скриншот")
        self.retake_btn.clicked.connect(self._take_screenshot)
        self.reset_btn  = QPushButton("↩  Сбросить зоны")
        self.reset_btn.clicked.connect(self._reset)
        self.save_btn   = QPushButton("✔  Сохранить калибровку")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.retake_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

        self._update_hint()

    # ---------------------------------------------------------------- #

    def _take_screenshot(self):
        import time
        time.sleep(0.3)
        pil_img = pyautogui.screenshot()
        self._pil_img = pil_img

        # Convert PIL → QPixmap
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        qimg = QImage.fromData(buf.read())
        pixmap = QPixmap.fromImage(qimg)

        # Replace placeholder with canvas
        layout = self.layout()
        idx = layout.indexOf(self.canvas_placeholder)
        if hasattr(self, "canvas") and self.canvas:
            layout.removeWidget(self.canvas)
            self.canvas.deleteLater()
        layout.removeWidget(self.canvas_placeholder)
        self.canvas_placeholder.hide()

        self.canvas = CalibrationCanvas(pixmap, pil_img.width, pil_img.height, self)
        self.canvas.rect_drawn.connect(self._on_rect_drawn)
        layout.insertWidget(idx, self.canvas, stretch=1)

        self._reset()

    def _on_rect_drawn(self, name: str, rect: QRect):
        self._rects[name] = rect
        self._update_hint()

    def _reset(self):
        self._rects = {}
        if hasattr(self, "canvas") and self.canvas:
            self.canvas.finished_rects = {}
            self.canvas.current_step = 0
            self.canvas.update()
        self._update_hint()

    def _update_hint(self):
        if not hasattr(self, "canvas") or not self.canvas:
            self.hint_label.setText("Подождите, делаю скриншот экрана игры…")
            return

        step = self.canvas.current_step
        if step < len(STEPS):
            _, color, text = STEPS[step]
            hex_color = color.name()
            self.hint_label.setText(
                f'<span style="color:{hex_color}; font-weight:bold;">Шаг {step+1}/3 — </span>{text}'
            )
        else:
            self.hint_label.setText(
                '✅ Все зоны отмечены! Укажите количество строк и нажмите <b>Сохранить калибровку</b>.'
            )

    # ---------------------------------------------------------------- #

    def _save(self):
        if len(self._rects) < 4:
            missing = {"badge", "name", "score", "badge2"} - set(self._rects.keys())
            mb_warning(self, "Не готово", f"Не хватает зон: {', '.join(missing)}")
            return

        row_count  = self.row_count_spin.value()
        badge_rect = self._rects["badge"]
        name_rect  = self._rects["name"]
        score_rect = self._rects["score"]
        badge2_rect = self._rects["badge2"]

        W = self._screen_w
        H = self._screen_h

        # Real row stride = distance between top of row1 badge and top of row2 badge
        row_stride_px = badge2_rect.y() - badge_rect.y()
        if row_stride_px <= 0:
            mb_warning(self, "Ошибка", "Вторая строка должна быть НИЖЕ первой.")
            return

        rows = []
        for i in range(row_count):
            dy = i * row_stride_px
            rows.append({
                "badge": {
                    "x": badge_rect.x() / W,
                    "y": (badge_rect.y() + dy) / H,
                    "w": badge_rect.width()  / W,
                    "h": badge_rect.height() / H,
                },
                "name": {
                    "x": name_rect.x() / W,
                    "y": (name_rect.y() + dy) / H,
                    "w": name_rect.width()  / W,
                    "h": name_rect.height() / H,
                },
                "score": {
                    "x": score_rect.x() / W,
                    "y": (score_rect.y() + dy) / H,
                    "w": score_rect.width()  / W,
                    "h": score_rect.height() / H,
                },
            })

        roi_profile = {
            "rows": rows,
            "row_stride_px": row_stride_px,
            "row_h_norm": badge_rect.height() / H,
        }
        save_roi_for_resolution(W, H, roi_profile)

        # Save debug crops so user can verify OCR sees the right areas
        self._save_debug_crops(rows, W, H)

        mb_info(
            self, "Сохранено",
            f"Калибровка для {W}×{H} сохранена!\n"
            f"{row_count} строк, шаг между строками: {row_stride_px}px\n\n"
            f"Отладочные кропы сохранены в папку debug_crops/ рядом с приложением.\n"
            f"Проверьте их — OCR должен видеть именно эти области."
        )
        self.calibration_saved.emit()
        self.accept()

    def _save_debug_crops(self, rows: list, screen_w: int, screen_h: int):
        """Save cropped zones as PNG so user can verify calibration visually."""
        import os
        from pathlib import Path
        debug_dir = Path("debug_crops")
        debug_dir.mkdir(exist_ok=True)

        if not hasattr(self, "_pil_img") or self._pil_img is None:
            return

        img = self._pil_img
        for i, row in enumerate(rows[:3]):  # save first 3 rows only
            for zone_name, rect in row.items():
                x = int(rect["x"] * screen_w)
                y = int(rect["y"] * screen_h)
                w = int(rect["w"] * screen_w)
                h = int(rect["h"] * screen_h)
                crop = img.crop((x, y, x + w, y + h))
                crop = crop.resize((crop.width * 3, crop.height * 3))  # 3x upscale for easy viewing
                crop.save(debug_dir / f"row{i+1}_{zone_name}.png")
