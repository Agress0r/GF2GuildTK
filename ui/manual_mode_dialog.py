"""
Manual mode dialog.

Lets the user load screenshots from files or clipboard,
then runs badge detection + OCR, shows results in a table,
and saves them to the database.
"""

from __future__ import annotations
from ui.controls import AppSpinBox

import io
import json
from pathlib import Path

from PIL import Image

from PyQt6.QtCore import Qt, QThread, QObject, QBuffer, QIODevice, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem, QTextEdit, QSplitter, QWidget, QFileDialog, QHeaderView, QAbstractItemView, QProgressBar, QApplication
from ui.controls import AppComboBox, ui_scale
from ui.style_utils import mb_warning, mb_info, mb_question
from ui.theme import DIALOG_STYLE, style_for, theme_color

from config.settings_manager import get_badge_offsets, load_settings
from db.database import import_ocr_scores, get_season, get_scores_for_season
from core.manual_processor import OcrResult


STYLESHEET = """
QLabel#hint {
    font-size: 12px; padding: 5px 10px;
    border-radius: 5px; background: #13141a;
    border-left: 3px solid #3a2b16;
    color: #8c8a7e;
}
QTextEdit {
    background: #0b0c0e; color: #a4a094;
    border: none; font-family: 'Consolas', monospace; font-size: 12px;
    padding: 6px;
}
"""


# ------------------------------------------------------------------ #
#  Worker                                                              #
# ------------------------------------------------------------------ #

class _ProcessWorker(QObject):
    result_row   = pyqtSignal(object)
    log_message  = pyqtSignal(str)
    progress     = pyqtSignal(int, int)
    finished     = pyqtSignal()

    def __init__(self, images: list[Image.Image], offsets):
        super().__init__()
        self.images  = images
        self.offsets = offsets
        self._cancelled = False

    def stop(self):
        self._cancelled = True

    def run(self):
        from core.manual_processor import process_images
        try:
            results = process_images(
                self.images,
                self.offsets,
                log=self.log_message.emit,
                progress=self.progress.emit,
                should_cancel=lambda: self._cancelled,
            )
            for result in results:
                self.result_row.emit(result)
        except Exception as exc:
            self.log_message.emit(f"❌ Обработка прервана: {exc}")
        finally:
            self.finished.emit()


# ------------------------------------------------------------------ #
#  Dialog                                                              #
# ------------------------------------------------------------------ #

class _DropImageList(QListWidget):
    files_dropped = pyqtSignal(list)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        self.files_dropped.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()


class ManualModeDialog(QDialog):
    def __init__(self, season_id: int, season_name: str, parent=None):
        super().__init__(parent)
        self.season_id   = season_id
        self.season_name = season_name

        self.setWindowTitle(f"Ручной режим — {season_name}")
        self.setStyleSheet(style_for(DIALOG_STYLE + STYLESHEET))
        self.setMinimumSize(1050, 680)

        # loaded PIL images, parallel to list widget items
        self._images: list[Image.Image] = []
        # processing results: [(name, score)]
        self._results: list[OcrResult] = []
        self._db_scores = {row["name"]: row["total_score"] for row in get_scores_for_season(season_id)}
        self._draft_dir = Path(load_settings()["db_path"]).resolve().parent / "drafts" / f"season_{season_id}"
        self._restoring = False
        self._draft_complete = False
        self._images_dirty = False

        self._thread: QThread | None = None
        self._worker: _ProcessWorker | None = None

        self._build_ui()
        self.setAcceptDrops(True)
        self.result_table.itemChanged.connect(self._on_result_edited)
        self._restore_draft()
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
            "Перетащите готовые скриншоты сюда, откройте файлы или вставьте изображение (Ctrl+V). "
            "Приложение автоматически найдёт бейджи (номер слева от профиля), распознает имена и финальный счёт."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Body splitter: left = image list, right = results
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet(style_for("QSplitter::handle { background: #22242c; width: 2px; }"))
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

        self.img_list = _DropImageList()
        self.img_list.setAcceptDrops(True)
        self.img_list.files_dropped.connect(self._add_files)
        self.img_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.addWidget(self.img_list, stretch=1)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("Открыть файлы")
        load_btn.clicked.connect(self._load_files)

        remove_btn = QPushButton("✕ Удалить")
        remove_btn.setObjectName("remove_btn")
        remove_btn.clicked.connect(self._remove_selected)

        btn_row.addWidget(load_btn)
        v.addLayout(btn_row)
        v.addWidget(remove_btn)

        self.img_count_label = QLabel("0 изображений")
        self.img_count_label.setStyleSheet(style_for("color: #8c8a7e; font-size: 12px;"))
        v.addWidget(self.img_count_label)

        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 0, 0, 0)
        v.setSpacing(6)

        self.progress = QProgressBar()
        self.progress.setMaximum(1)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(style_for("QSplitter::handle { background: #22242c; height: 2px; }"))

        # Results table
        table_widget = QWidget()
        tv = QVBoxLayout(table_widget)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        lbl = QLabel("  Результаты:")
        lbl.setStyleSheet(style_for("background: #13141a; color: #8c8a7e; font-size: 12px; padding: 4px 6px; border-bottom: 1px solid #22242c;"))
        tv.addWidget(lbl)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["#", "Игрок", "Total Score", "Проверка"])
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.result_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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
        lbl2.setStyleSheet(style_for("background: #13141a; color: #8c8a7e; font-size: 12px; padding: 4px 6px; border-bottom: 1px solid #22242c;"))
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
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 4, 0, 0)
        h = QHBoxLayout()

        self.status_label = QLabel("Загрузите скриншоты и нажмите «Обработать»")
        self.status_label.setStyleSheet(style_for("color: #8c8a7e; font-size: 12px;"))
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)
        outer.addLayout(h)

        # Day selector
        h.addWidget(QLabel("День:"))
        self.day_spin = AppComboBox()
        for d in range(1, 8):
            self.day_spin.addItem(str(d), d)
        self.day_spin.setCurrentIndex(self._suggest_day() - 1)
        self.day_spin.setFixedWidth(round(72 * ui_scale()))
        self.day_spin.setToolTip("День сезона, для которого сохраняются данные (1–7)")
        h.addWidget(self.day_spin)

        h.addWidget(QLabel("Масштаб UI:"))
        self.scale_spin = AppSpinBox()
        self.scale_spin.setRange(50, 200)
        self.scale_spin.setSingleStep(5)
        self.scale_spin.setValue(100)
        self.scale_spin.setSuffix("%")
        self.scale_spin.setToolTip("Масштаб интерфейса игры для выбора профиля калибровки")
        h.addWidget(self.scale_spin)

        calib_btn = QPushButton("🔧 Калибровка смещений")
        calib_btn.clicked.connect(self._open_calibration)
        h.addWidget(calib_btn)

        self.process_btn = QPushButton("▶  Обработать")
        self.process_btn.setObjectName("process_btn")
        self.process_btn.clicked.connect(self._start_processing)
        h.addWidget(self.process_btn)

        self.cancel_btn = QPushButton("Остановить")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_processing)
        h.addWidget(self.cancel_btn)

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
        self._add_files(paths)

    def _add_files(self, paths):
        for path in paths:
            try:
                img = Image.open(path).convert("RGB")
                self._add_image(img, Path(path).name)
            except Exception as e:
                self._log(f"⚠ Не удалось загрузить {path}: {e}")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        self._add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def _add_image(self, img: Image.Image, label: str):
        self._images.append(img)
        self._images_dirty = True
        item = QListWidgetItem(f"🖼  {label}  ({img.width}×{img.height})")
        item.setData(Qt.ItemDataRole.UserRole, label)
        self.img_list.addItem(item)
        self._update_img_count()
        self._save_draft()

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
        self._images_dirty = True
        self._update_img_count()
        self._save_draft()

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

        scale = self.scale_spin.value()
        missing = [image.size for image in self._images
                   if not get_badge_offsets(image.width, image.height, scale)]
        if missing:
            if mb_question(self, "Нет калибровки",
                           f"Нет профиля для {missing[0][0]}×{missing[0][1]} при {scale}%.\n"
                           "Открыть калибровку?"):
                self._open_calibration()
            return

        self._results.clear()
        self.result_table.setRowCount(0)
        self.log.clear()
        self.save_btn.setEnabled(False)
        self.process_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setMaximum(len(self._images))
        self.progress.setValue(0)
        self.status_label.setText("Обработка…")

        self._worker = _ProcessWorker(
            list(self._images),
            lambda image: get_badge_offsets(image.width, image.height, scale),
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.result_row.connect(self._on_result_row)
        self._worker.log_message.connect(self._log)
        self._worker.progress.connect(lambda done, total: self.progress.setValue(done))
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit, Qt.ConnectionType.DirectConnection)
        self._thread.start()

    def _cancel_processing(self):
        if self._worker:
            self._worker.stop()
            self.status_label.setText("Останавливаю обработку…")
            self.cancel_btn.setEnabled(False)

    def _on_result_row(self, result: OcrResult):
        self._restoring = True
        self._results.append(result)
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        _ro = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        rank_item = QTableWidgetItem(str(row + 1))
        rank_item.setFlags(_ro)
        name_item = QTableWidgetItem(result.name)
        score_item = QTableWidgetItem(f"{result.score:,}")
        score_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.result_table.setItem(row, 0, rank_item)
        self.result_table.setItem(row, 1, name_item)
        self.result_table.setItem(row, 2, score_item)
        warnings = []
        if result.confidence < 0.72:
            warnings.append(f"OCR {result.confidence:.0%}")
        if result.occurrences > 1:
            warnings.append(f"Дубль ×{result.occurrences}")
        previous = self._db_scores.get(result.name)
        if previous is not None and result.score > max(previous * 2, previous + 100_000):
            warnings.append(f"Скачок с {previous:,}")
        review = QTableWidgetItem(" · ".join(warnings) if warnings else "✓ Проверено")
        review.setFlags(_ro)
        if warnings:
            review.setForeground(theme_color(QColor("#e8b84b")))
            review.setBackground(theme_color(QColor("#332919")))
            review.setToolTip("Проверьте имя и счёт перед сохранением")
        self.result_table.setItem(row, 3, review)
        self.result_table.scrollToBottom()
        self._restoring = False
        self._save_draft()

    def _on_result_edited(self, item: QTableWidgetItem):
        if self._restoring or item.column() not in (1, 2):
            return
        status = self.result_table.item(item.row(), 3)
        if status:
            self._restoring = True
            status.setText("Исправлено вручную")
            status.setForeground(theme_color(QColor("#9ad7aa")))
            self._restoring = False
        self._save_draft()

    def _on_finished(self):
        self.progress.setVisible(False)
        self.process_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
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

        flagged = sum(1 for row in range(self.result_table.rowCount())
                      if self.result_table.item(row, 3).text() != "✓ Проверено")
        if flagged and not mb_question(
            self, "Проверка OCR", f"Есть {flagged} записей с предупреждениями. Вы проверили их?"
        ):
            return
        day_number = self.day_spin.currentData()
        rows = []
        try:
            for rank, _ in enumerate(self._results, start=1):
                name = (self.result_table.item(rank - 1, 1) or QTableWidgetItem("")).text().strip()
                raw = self.result_table.item(rank - 1, 2).text()
                score = int(raw.replace(",", "").replace(" ", ""))
                rows.append((name, score))
            saved = import_ocr_scores(self.season_id, day_number, rows)
        except Exception as exc:
            mb_warning(self, "Ошибка сохранения", str(exc))
            return

        if saved == len(self._results):
            self._draft_complete = True
            self._clear_draft()

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

    def _save_draft(self):
        if self._restoring or self._draft_complete:
            return
        if not self._images and not self._results:
            self._clear_draft()
            return
        self._draft_dir.mkdir(parents=True, exist_ok=True)
        images = []
        for index, image in enumerate(self._images):
            name = f"image_{index:03d}.png"
            target = self._draft_dir / name
            if self._images_dirty or not target.exists():
                image.save(target, format="PNG")
            images.append({"file": name,
                           "label": self.img_list.item(index).data(Qt.ItemDataRole.UserRole)})
        keep = {entry["file"] for entry in images}
        for stale in self._draft_dir.glob("image_*.png"):
            if stale.name not in keep:
                stale.unlink()
        self._images_dirty = False
        results = []
        for index, result in enumerate(self._results):
            results.append({
                "name": self.result_table.item(index, 1).text(),
                "score": self.result_table.item(index, 2).text(),
                "confidence": result.confidence,
                "occurrences": result.occurrences,
                "score_variants": sorted(result.score_variants),
                "status": self.result_table.item(index, 3).text(),
            })
        payload = {"images": images, "results": results,
                   "day": self.day_spin.currentData(), "scale": self.scale_spin.value()}
        temp = self._draft_dir / "draft.tmp"
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(self._draft_dir / "draft.json")

    def _restore_draft(self):
        path = self._draft_dir / "draft.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._restoring = True
            self.day_spin.setCurrentIndex(max(0, min(6, int(data.get("day", 1)) - 1)))
            self.scale_spin.setValue(int(data.get("scale", 100)))
            for entry in data.get("images", []):
                image = Image.open(self._draft_dir / entry["file"]).convert("RGB")
                self._add_image(image, entry["label"])
            self._images_dirty = False
            self._restoring = False
            for entry in data.get("results", []):
                result = OcrResult(
                    entry["name"], int(entry["score"].replace(",", "").replace(" ", "")),
                    float(entry["confidence"]), int(entry["occurrences"]),
                    set(entry.get("score_variants", [])),
                )
                self._on_result_row(result)
                self.result_table.item(self.result_table.rowCount() - 1, 3).setText(entry["status"])
            self.save_btn.setEnabled(bool(self._results))
            self.status_label.setText("Незавершённый черновик восстановлен.")
        except Exception as exc:
            self._restoring = False
            self.status_label.setText(f"Не удалось восстановить черновик: {exc}")

    def _clear_draft(self):
        if not self._draft_dir.exists():
            return
        for path in self._draft_dir.iterdir():
            if path.is_file() and (path.name in {"draft.json", "draft.tmp"} or
                                   path.name.startswith("image_") and path.suffix == ".png"):
                path.unlink()
        try:
            self._draft_dir.rmdir()
        except OSError:
            pass

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            self._worker.stop()
            self._thread.quit()
            self._thread.wait()
        self._save_draft()
        super().closeEvent(event)
