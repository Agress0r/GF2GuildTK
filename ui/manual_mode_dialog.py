"""
Manual mode dialog.

Lets the user load screenshots from files or capture them in-app,
then runs badge detection + OCR, shows results in a table,
and saves them to the database.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

from PIL import Image

from PyQt6.QtCore import Qt, QThread, QObject, QBuffer, QIODevice, pyqtSignal, QTimer, QProcess
from PyQt6.QtGui import QPixmap, QImage, QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QTextEdit, QSplitter, QWidget, QFileDialog,
    QHeaderView, QAbstractItemView, QProgressBar, QApplication,
    QComboBox,
)
from ui.style_utils import mb_warning, mb_info, mb_question

from config.settings_manager import get_badge_offsets
from db.database import save_score, get_season, get_all_player_names_with_lock


STYLESHEET = """
QDialog {
    background: #0a0e17;
    color: #d0ddf0;
    font-family: 'Segoe UI', sans-serif;
}
QLabel { color: #d0ddf0; font-size: 13px; }
QLabel#title {
    font-size: 18px; font-weight: bold;
    color: #f0c040; padding: 6px 0;
}
QLabel#hint {
    font-size: 12px; padding: 5px 10px;
    border-radius: 5px; background: #131e30;
    border-left: 3px solid #2a5a9a;
    color: #8090b0;
}
QPushButton {
    background: #1e2840; color: #c8d8f0;
    border: 1px solid #2a3a5a; border-radius: 6px;
    padding: 7px 16px; font-size: 13px;
}
QPushButton:hover  { background: #2a3a5a; }
QPushButton:pressed{ background: #384870; }
QPushButton#process_btn {
    background: #1a4a7a; border-color: #3a7abf;
    color: #fff; font-weight: bold;
}
QPushButton#process_btn:hover { background: #2a5a9a; }
QPushButton#process_btn:disabled { background: #111820; color: #405060; border-color: #1a2030; }
QPushButton#save_btn {
    background: #1a4a2a; border-color: #2a7a4a;
    color: #80ff80; font-weight: bold;
}
QPushButton#save_btn:hover { background: #206030; }
QPushButton#save_btn:disabled { background: #111820; color: #405060; border-color: #1a2030; }
QPushButton#remove_btn {
    background: #3a1a1a; color: #ff8080;
    border-color: #5a2a2a; padding: 4px 10px;
    font-size: 12px;
}
QPushButton#capture_btn {
    background: #2a1a3a; border-color: #5a3a7a; color: #c0a0e0;
}
QListWidget {
    background: #0f1520; border: 1px solid #1e2a40;
    border-radius: 4px; color: #a0b8d0; font-size: 12px;
}
QListWidget::item:selected { background: #1a2a40; }
QTableWidget {
    background: #0d1625; color: #c0d4f0;
    gridline-color: #162030; border: none; font-size: 13px;
}
QHeaderView::section {
    background: #0d1a2a; color: #7090b0;
    border: none; border-bottom: 1px solid #1e2a40;
    padding: 5px 10px; font-size: 12px; font-weight: bold;
}
QTableWidget::item { padding: 3px 8px; border-bottom: 1px solid #111a27; }
QTextEdit {
    background: #080e18; color: #4a8a4a;
    border: none; font-family: 'Consolas', monospace; font-size: 12px;
    padding: 6px;
}
QProgressBar {
    background: #111a28; border: 1px solid #1e2a40;
    border-radius: 4px; height: 5px;
}
QProgressBar::chunk { background: #2a7a4a; border-radius: 4px; }
"""


# ------------------------------------------------------------------ #
#  Worker                                                              #
# ------------------------------------------------------------------ #

class _ProcessWorker(QObject):
    result_row   = pyqtSignal(str, int)   # name, score
    log_message  = pyqtSignal(str)
    finished     = pyqtSignal()

    def __init__(self, images: list[Image.Image], offsets: dict):
        super().__init__()
        self.images  = images
        self.offsets = offsets

    def run(self):
        from core.manual_processor import process_images
        results = process_images(
            self.images,
            self.offsets,
            log=self.log_message.emit,
        )
        for name, score in results:
            self.result_row.emit(name, score)
        self.finished.emit()


# ------------------------------------------------------------------ #
#  Dialog                                                              #
# ------------------------------------------------------------------ #

class ManualModeDialog(QDialog):
    def __init__(self, season_id: int, season_name: str, parent=None):
        super().__init__(parent)
        self.season_id   = season_id
        self.season_name = season_name

        self.setWindowTitle(f"Ручной режим — {season_name}")
        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(1050, 680)

        # loaded PIL images, parallel to list widget items
        self._images: list[Image.Image] = []
        # processing results: [(name, score)]
        self._results: list[tuple[str, int]] = []

        self._thread: QThread | None = None
        self._worker: _ProcessWorker | None = None

        self._build_ui()
        QShortcut(QKeySequence("Ctrl+V"), self).activated.connect(self._paste_clipboard)

    # ---------------------------------------------------------------- #
    #  UI construction                                                   #
    # ---------------------------------------------------------------- #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Title + hint
        title = QLabel("Ручной режим — загрузка скриншотов")
        title.setObjectName("title")
        root.addWidget(title)

        hint = QLabel(
            "Загрузите скриншоты Гильд-таблицы или сделайте их в приложении с помощью инструмента <b>Захват</b>. "
            "Приложение автоматически найдёт бейджи (номер слева от профиля), распознает имена и финальный счёт."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Body splitter: left = image list, right = results
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet("QSplitter::handle { background: #1e2a40; width: 2px; }")
        root.addWidget(body, stretch=1)

        body.addWidget(self._build_left_panel())
        body.addWidget(self._build_right_panel())
        body.setSizes([280, 760])

        # Bottom bar
        root.addWidget(self._build_bottom_bar())

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("sidebar")
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(6)

        v.addWidget(QLabel("Скриншоты:"))

        self.img_list = QListWidget()
        self.img_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.addWidget(self.img_list, stretch=1)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("📂")
        load_btn.clicked.connect(self._load_files)

        capture_btn = QPushButton("✂ Захват")
        capture_btn.setObjectName("capture_btn")
        capture_btn.clicked.connect(self._open_snip)

        remove_btn = QPushButton("✕ Удалить")
        remove_btn.setObjectName("remove_btn")
        remove_btn.clicked.connect(self._remove_selected)

        btn_row.addWidget(load_btn)
        btn_row.addWidget(capture_btn)
        v.addLayout(btn_row)
        v.addWidget(remove_btn)

        self.img_count_label = QLabel("0 изображений")
        self.img_count_label.setStyleSheet("color: #4a6a8a; font-size: 12px;")
        v.addWidget(self.img_count_label)

        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 0, 0, 0)
        v.setSpacing(6)

        self.progress = QProgressBar()
        self.progress.setMaximum(0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet("QSplitter::handle { background: #1e2a40; height: 2px; }")

        # Results table
        table_widget = QWidget()
        tv = QVBoxLayout(table_widget)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        lbl = QLabel("  Результаты:")
        lbl.setStyleSheet("background: #0f1520; color: #3a5a7a; font-size: 12px; padding: 4px 6px; border-bottom: 1px solid #1e2a40;")
        tv.addWidget(lbl)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["#", "Игрок", "Total Score"])
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.result_table.verticalHeader().setVisible(False)
        tv.addWidget(self.result_table, stretch=1)
        splitter.addWidget(table_widget)

        # Log
        log_widget = QWidget()
        lv = QVBoxLayout(log_widget)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        lbl2 = QLabel("  Журнал обработки:")
        lbl2.setStyleSheet("background: #0f1520; color: #3a5a7a; font-size: 12px; padding: 4px 6px; border-bottom: 1px solid #1e2a40;")
        lv.addWidget(lbl2)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        lv.addWidget(self.log, stretch=1)
        splitter.addWidget(log_widget)

        splitter.setSizes([380, 180])
        v.addWidget(splitter, stretch=1)
        return panel

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 4, 0, 0)

        self.status_label = QLabel("Загрузите скриншоты и нажмите «Обработать»")
        self.status_label.setStyleSheet("color: #4a6a8a; font-size: 12px;")
        h.addWidget(self.status_label, stretch=1)

        # Day selector
        h.addWidget(QLabel("День:"))
        self.day_spin = QComboBox()
        for d in range(1, 8):
            self.day_spin.addItem(str(d), d)
        self.day_spin.setCurrentIndex(self._suggest_day() - 1)
        self.day_spin.setFixedWidth(64)
        self.day_spin.setToolTip("День сезона, для которого сохраняются данные (1–7)")
        self.day_spin.setStyleSheet(
            "QComboBox { background:#1a2035; color:#d0ddf0; border:1px solid #2a3a5a;"
            " border-radius:4px; padding:4px 6px; font-size:10pt; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { background:#1a2035; color:#d0ddf0;"
            " selection-background-color:#2a3a5a; font-size:10pt; }"
        )
        h.addWidget(self.day_spin)

        calib_btn = QPushButton("🔧 Калибровка смещений")
        calib_btn.clicked.connect(self._open_calibration)
        h.addWidget(calib_btn)

        self.process_btn = QPushButton("▶  Обработать")
        self.process_btn.setObjectName("process_btn")
        self.process_btn.clicked.connect(self._start_processing)
        h.addWidget(self.process_btn)

        self.save_btn = QPushButton("💾  Сохранить в базу данных")
        self.save_btn.setObjectName("save_btn")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_to_db)
        h.addWidget(self.save_btn)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        h.addWidget(close_btn)

        return bar

    def _suggest_day(self) -> int:
        """Auto-suggest the current day number based on season start_date."""
        try:
            season = get_season(self.season_id)
            if season and season.get("start_date"):
                from datetime import datetime, date as date_type
                start = datetime.strptime(season["start_date"], "%Y-%m-%d").date()
                delta = (date_type.today() - start).days + 1
                return max(1, min(7, delta))
        except Exception:
            pass
        return 1

    # ---------------------------------------------------------------- #
    #  Image management                                                  #
    # ---------------------------------------------------------------- #

    def _load_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите скриншоты", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        for path in paths:
            try:
                img = Image.open(path).convert("RGB")
                self._add_image(img, path.split("/")[-1].split("\\")[-1])
            except Exception as e:
                self._log(f"⚠ Не удалось загрузить {path}: {e}")

    def _open_snip(self):
        print("[DEBUG] _open_snip() called", flush=True)
        # Не скрываем диалог - пусть overlay появится на top
        print("[DEBUG] About to call _do_snip() directly", flush=True)
        self._do_snip()

    def _restore_windows(self):
        QApplication.instance().setQuitOnLastWindowClosed(True)
        for w in getattr(self, "_hidden_windows", []):
            try:
                w.show()
            except RuntimeError:
                pass
        self._hidden_windows = []

    def _do_snip(self):
        try:
            print("[DEBUG] _do_snip() called", flush=True)
            # Hide all visible app windows so the overlay has a clean desktop
            self._hidden_windows = [
                w for w in QApplication.topLevelWidgets() if w.isVisible()
            ]
            QApplication.instance().setQuitOnLastWindowClosed(False)
            for w in self._hidden_windows:
                w.hide()
            QApplication.processEvents()  # let the OS repaint before screenshot

            self._snip_tmp = tempfile.mktemp(suffix=".png")
            script = os.path.join(os.path.dirname(__file__), "snip_proc.py")
            print(f"[DEBUG] Script path: {script}", flush=True)
            # Create QProcess WITHOUT parent to prevent destruction
            self._snip_proc = QProcess()
            # Set working directory to project root
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self._snip_proc.setWorkingDirectory(project_root)
            self._snip_proc.finished.connect(self._snip_finished)
            self._snip_proc.errorOccurred.connect(self._snip_error)
            self._log(f"🔧 Запуск snip_proc.py в {project_root}")
            print(f"[DEBUG] Starting process: {sys.executable} {script} {self._snip_tmp}", flush=True)
            self._snip_proc.start(sys.executable, [script, self._snip_tmp])
            if not self._snip_proc.waitForStarted(3000):
                self.show()
                self.status_label.setText("⚠ Не удалось запустить snipping tool")
                self._log("❌ Ошибка: не удалось запустить процесс")
        except Exception as e:
            print(f"[ERROR] Exception in _do_snip(): {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            self.show()
            self.status_label.setText(f"⚠ Внутренняя ошибка: {e}")

    def _snip_finished(self, exit_code: int, _exit_status):
        self._log(f"📦 snip_proc завершился с кодом {exit_code}")
        if exit_code == 0 and os.path.exists(self._snip_tmp):
            try:
                pil = Image.open(self._snip_tmp).copy()
                self._log(f"✓ Загружен снимок {pil.width}×{pil.height}")
                self._restore_windows()
                self._on_snipped(pil)
            except Exception as e:
                self._restore_windows()
                self.status_label.setText(f"⚠ Ошибка чтения снимка: {e}")
                self._log(f"❌ Ошибка открытия снимка: {e}")
            finally:
                try:
                    os.unlink(self._snip_tmp)
                except OSError:
                    pass
        else:
            self._restore_windows()
            if exit_code != 0:
                self._log(f"⚠ Процесс вернул код ошибки {exit_code}")
                stderr = self._snip_proc.readAllStandardError().data().decode('utf-8', errors='ignore')
                if stderr:
                    self._log(f"Ошибка процесса: {stderr}")
            elif not os.path.exists(self._snip_tmp):
                self._log(f"⚠ Файл не сохранен: {self._snip_tmp}")
            try:
                os.unlink(self._snip_tmp)
            except OSError:
                pass

    def _snip_error(self, error):
        self._restore_windows()
        error_msg = self._snip_proc.errorString()
        self.status_label.setText(f"⚠ Ошибка процесса: {error_msg}")
        self._log(f"❌ Ошибка QProcess: {error_msg}")

    def _on_snipped(self, pil_crop: Image.Image):
        idx = len(self._images) + 1
        self._add_image(pil_crop, f"snip_{idx}.png")
        self.status_label.setText("Область добавлена.")

    def _add_image(self, img: Image.Image, label: str):
        self._images.append(img)
        item = QListWidgetItem(f"🖼  {label}  ({img.width}×{img.height})")
        self.img_list.addItem(item)
        self._update_img_count()

    def _paste_clipboard(self):
        clipboard = QApplication.clipboard()
        qimg = clipboard.image()
        if qimg.isNull():
            self.status_label.setText("⚠ Буфер обмена не содержит изображения.")
            return
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        qimg.save(buf, "PNG")
        buf.close()
        pil = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGB")
        idx = len(self._images) + 1
        self._add_image(pil, f"clipboard_{idx}.png")
        self.status_label.setText("Изображение вставлено из буфера обмена.")

    def _remove_selected(self):
        rows = sorted(
            {self.img_list.row(it) for it in self.img_list.selectedItems()},
            reverse=True
        )
        for r in rows:
            self.img_list.takeItem(r)
            self._images.pop(r)
        self._update_img_count()

    def _update_img_count(self):
        n = len(self._images)
        self.img_count_label.setText(f"{n} изображени{'е' if n == 1 else 'й' if 2 <= n <= 4 else 'й'}")

    # ---------------------------------------------------------------- #
    #  Processing                                                        #
    # ---------------------------------------------------------------- #

    def _start_processing(self):
        if not self._images:
            mb_warning(self, "Нет изображений", "Загрузите хотя бы один скриншот.")
            return

        offsets = get_badge_offsets()
        if not offsets:
            if mb_question(self, "Нет калибровки",
                           "Калибровка смещений не настроена.\nОткрыть калибровку?"):
                self._open_calibration()
            return

        self._results.clear()
        self.result_table.setRowCount(0)
        self.log.clear()
        self.save_btn.setEnabled(False)
        self.process_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status_label.setText("Обработка…")

        self._worker = _ProcessWorker(list(self._images), offsets)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.result_row.connect(self._on_result_row)
        self._worker.log_message.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_result_row(self, name: str, score: int):
        self._results.append((name, score))
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        _ro = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        rank_item = QTableWidgetItem(str(row + 1))
        rank_item.setFlags(_ro)
        name_item = QTableWidgetItem(name)
        score_item = QTableWidgetItem(f"{score:,}")
        score_item.setFlags(_ro)
        score_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.result_table.setItem(row, 0, rank_item)
        self.result_table.setItem(row, 1, name_item)
        self.result_table.setItem(row, 2, score_item)
        self.result_table.scrollToBottom()

    def _on_finished(self):
        self.progress.setVisible(False)
        self.process_btn.setEnabled(True)
        n = len(self._results)
        self.status_label.setText(f"Готово — найдено {n} игрок{'а' if 2 <= n <= 4 else 'ов' if n != 1 else ''}")
        if n > 0:
            self.save_btn.setEnabled(True)
        if self._thread:
            self._thread.quit()
            self._thread.wait()

    # ---------------------------------------------------------------- #
    #  Save to DB                                                        #
    # ---------------------------------------------------------------- #

    def _save_to_db(self):
        if not self._results:
            return

        day_number = self.day_spin.currentData()
        existing_players = get_all_player_names_with_lock()
        saved = 0
        for rank, (_, score) in enumerate(self._results, start=1):
            name = (self.result_table.item(rank - 1, 1) or QTableWidgetItem("")).text().strip()
            if not name:
                self._log(f"⚠ Строка {rank}: пустое имя — пропуск")
                continue
            try:
                save_score(self.season_id, name, score, rank,
                           day_number=day_number,
                           existing_players=existing_players)
                saved += 1
            except Exception as e:
                self._log(f"❌ Ошибка сохранения {name!r}: {e}")

        self.status_label.setText(f"Сохранено {saved}/{len(self._results)} записей в сезон «{self.season_name}»")
        self._log(f"\n💾 Сохранено в БД: {saved} игроков")
        self.save_btn.setEnabled(False)
        mb_info(
            self, "Сохранено",
            f"Сохранено {saved} игроков в сезон «{self.season_name}»."
        )

    # ---------------------------------------------------------------- #
    #  Calibration                                                       #
    # ---------------------------------------------------------------- #

    def _open_calibration(self):
        from ui.badge_calibration import BadgeOffsetCalibrationDialog
        dlg = BadgeOffsetCalibrationDialog(self)
        dlg.exec()

    # ---------------------------------------------------------------- #
    #  Log                                                               #
    # ---------------------------------------------------------------- #

    def _log(self, text: str):
        self.log.append(text)
