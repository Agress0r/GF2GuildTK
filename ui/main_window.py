"""Main application window."""

from __future__ import annotations
from ui.controls import AppSpinBox, ui_scale
import os
import sys
from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QSplitter, QHeaderView, QAbstractItemView, QProgressBar, QMenu, QLineEdit, QDialog, QRadioButton, QDialogButtonBox
from ui.style_utils import mb_warning, mb_critical, mb_info, mb_question, ask_text
from ui.theme import DIALOG_STYLE, style_for, theme_color
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence, QPixmap, QShortcut

from db.database import (
    init_db, get_scores_for_season,
    rename_player, set_player_locked, get_player_lock_state,
    save_day_score, get_last_active_season,
    add_player_to_season, remove_player_from_season
)
from ui.settings_dialog import SettingsDialog
from ui.season_dialog import SeasonDialog
from ui.manual_mode_dialog import ManualModeDialog
from ui.badge_calibration import BadgeOffsetCalibrationDialog
from ui.history_dialog import HistoryDialog
from db.backups import latest_import, restore_latest_import


# ------------------------------------------------------------------ #
#  Worker thread                                                       #
# ------------------------------------------------------------------ #

class _SheetsFetchWorker(QThread):
    """Phase 1: reads the sheet, builds diff — no writes."""
    finished = pyqtSignal(object)   # SyncPreview
    error    = pyqtSignal(str)

    def __init__(self, creds_path: str, sheet_id: str, season_id: int, worksheet_id: int):
        super().__init__()
        self.creds_path = creds_path
        self.sheet_id   = sheet_id
        self.season_id  = season_id
        self.worksheet_id = worksheet_id

    def run(self):
        try:
            from core.sheets_sync import prepare_sync
            preview = prepare_sync(self.creds_path, self.sheet_id, self.season_id,
                                   worksheet_id=self.worksheet_id)
            self.finished.emit(preview)
        except Exception as e:
            self.error.emit(str(e))


class _SheetsWriteWorker(QThread):
    """Phase 2: writes pre-built cells to the sheet."""
    finished = pyqtSignal(object)   # SyncResult
    error    = pyqtSignal(str)

    def __init__(self, preview):
        super().__init__()
        self._preview = preview

    def run(self):
        try:
            from core.sheets_sync import execute_sync
            result = execute_sync(self._preview)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class _SheetsImportFetchWorker(QThread):
    """Phase 1 reverse: reads sheet, builds ReversePreview — no DB writes."""
    finished = pyqtSignal(object)   # ReversePreview
    error    = pyqtSignal(str)

    def __init__(self, creds_path: str, sheet_id: str, season_id: int,
                 worksheet_id: int):
        super().__init__()
        self.creds_path = creds_path
        self.sheet_id   = sheet_id
        self.season_id  = season_id
        self.worksheet_id = worksheet_id

    def run(self):
        try:
            from core.sheets_sync import prepare_reverse_sync
            preview = prepare_reverse_sync(
                self.creds_path, self.sheet_id, self.season_id,
                worksheet_id=self.worksheet_id,
            )
            self.finished.emit(preview)
        except Exception as e:
            self.error.emit(str(e))


class _SheetsImportTabsWorker(QThread):
    """Load the current visible worksheet list without blocking the UI."""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, creds_path: str, sheet_id: str, season_id: int):
        super().__init__()
        self.creds_path = creds_path
        self.sheet_id = sheet_id
        self.season_id = season_id

    def run(self):
        try:
            from core.sheets_sync import list_import_worksheets
            worksheets = list_import_worksheets(self.creds_path, self.sheet_id)
            if not worksheets:
                raise ValueError("В таблице нет видимых листов для импорта.")
            self.finished.emit(worksheets)
        except Exception as exc:
            self.error.emit(str(exc))


class _SheetsImportWriteWorker(QThread):
    """Phase 2 reverse: writes snapshots from ReversePreview to SQLite."""
    finished = pyqtSignal(object)   # ReverseResult
    error    = pyqtSignal(str)

    def __init__(self, preview):
        super().__init__()
        self._preview = preview

    def run(self):
        try:
            from core.sheets_sync import execute_reverse_sync
            result = execute_reverse_sync(self._preview)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ------------------------------------------------------------------ #
#  Stylesheet                                                          #
# ------------------------------------------------------------------ #

STYLESHEET = DIALOG_STYLE + """
QMainWindow, QWidget#central { background: #0b0c0e; color: #ccc8bc; font-family: 'Segoe UI', sans-serif; }
QWidget#topbar { background: #0f1014; border-bottom: 1px solid #2a2a2b; }
QLabel#app_title { color: #e8b84b; font-size: 18px; font-weight: 700; letter-spacing: 2px; }
QLabel#season_label { color: #8c8a7e; font-size: 12px; padding-left: 12px; }
QLabel#metric { color: #ece8dc; font-size: 13px; font-weight: 600; padding: 0 12px; }
QWidget#sidebar { background: #0f1014; border-right: 1px solid #22242c;  }
QLabel#nav_label { color: #777267; font-size: 10px; font-weight: 700; letter-spacing: 2px; padding: 17px 10px 5px 10px; border-top: 1px solid #22242c; }
QPushButton { background: #17181d; color: #ccc8bc; border: 1px solid #353840; border-radius: 3px; padding: 9px 12px; font-size: 12px; text-align: left; }
QPushButton:hover { background: #242329; color: #ece8dc; border-color: #66502b; }
QPushButton:pressed { background: #33291b; }
QPushButton#start_btn, QPushButton#import_btn { background: #2a2115; color: #e8b84b; border: 1px solid #8a6118; font-weight: 700; }
QPushButton#start_btn:hover, QPushButton#import_btn:hover { background: #3a2b16; }
QPushButton#sheets_btn, QPushButton#seasons_btn { background: #17181d; color: #ccc8bc; }
QPushButton#search_clear { padding: 7px 10px; }
QLineEdit#search { background: #13141a; color: #ece8dc; border: 1px solid #353840; border-radius: 3px; padding: 8px 10px; font-size: 12px; selection-background-color: #8a6118; }
QLineEdit#search:focus { border-color: #c8922a; }
QLabel#content_title { color: #ece8dc; font-size: 15px; font-weight: 650; letter-spacing: 1px; }
QLabel#content_hint { color: #8c8a7e; font-size: 11px; }
QWidget#content_header { background: #101115; border-bottom: 1px solid #2a2a2b; }
QLabel#status_bar { background: #0f1014; color: #8c8a7e; border-top: 1px solid #22242c; font-size: 11px; padding: 3px 12px; }
QTableWidget { background: #0b0c0e; alternate-background-color: #101115; color: #ccc8bc; gridline-color: #22242c; border: none; font-size: 12px; selection-background-color: #332919; }
QHeaderView::section { background: #13141a; color: #a4a094; border: none; border-bottom: 1px solid #353840; padding: 10px 8px; font-size: 11px; font-weight: 700; }
QTableWidget::item { padding: 7px 8px; border-bottom: 1px solid #1d1f24; }
QTableWidget::item:selected { background: #332919; color: #ece8dc; }
QTextEdit#log { background: #0b0c0e; color: #a4a094; border: none; font-family: Consolas, monospace; font-size: 11px; padding: 10px; }
QProgressBar { background: #1d1f24; border: none; height: 4px; text-align: center; }
QProgressBar::chunk { background: #c8922a; }
QMenu { background: #17181d; color: #ccc8bc; border: 1px solid #353840; }
QMenu::item { padding: 7px 20px; }
QMenu::item:selected { background: #332919; color: #e8b84b; }
QSplitter::handle { background: #22242c; }
"""

class NumericItem(QTableWidgetItem):
    """QTableWidgetItem с числовой сортировкой по тексту (игнорирует запятые)."""
    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return int(self.text().replace(",", "")) < int(other.text().replace(",", ""))
        except ValueError:
            return super().__lt__(other)


# Column index constants
COL_POS   = 0
COL_NAME  = 1
COL_DAY1  = 2   # Day 1 delta
COL_DAY7  = 8   # Day 7 delta
COL_TOTAL = 9


# ------------------------------------------------------------------ #
#  Main Window                                                         #
# ------------------------------------------------------------------ #

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Guild Tracker")
        self.setMinimumSize(1100, 680)
        self.resize(1320, 780)
        self.setStyleSheet(style_for(STYLESHEET))

        self._current_season_id:   int | None = None
        self._current_season_name: str        = "Сезон не выбран"
        self._current_day:         int        = 1

        init_db()
        self._build_ui()
        self._install_shortcuts()
        self._auto_select_last_season()

    def refresh_theme(self):
        self.setStyleSheet(style_for(STYLESHEET))
        self.findChild(QWidget, "sidebar").setMinimumWidth(round(265 * ui_scale()))
        self._load_table()

    def _sheets_busy(self):
        return any(getattr(self, name, None) is not None and getattr(self, name).isRunning()
                   for name in ("_export_tabs", "_sheets_fetch", "_sheets_write",
                                "_import_tabs", "_import_fetch", "_import_write"))

    def closeEvent(self, event):
        if self._sheets_busy():
            self.status_bar.setText("Дождитесь завершения обмена с Google Таблицей.")
            event.ignore()
            return
        super().closeEvent(event)

    def _install_shortcuts(self):
        for keys, action in (
            ("Ctrl+F", self.search.setFocus),
            ("Ctrl+O", self._open_manual_mode),
            ("Ctrl+I", self._import_sheets),
            ("Ctrl+Shift+I", self._open_network_capture),
            ("Ctrl+E", self._export_sheets),
            ("Ctrl+H", self._open_history),
            ("Ctrl+Z", self._undo_last_import),
        ):
            QShortcut(QKeySequence(keys), self).activated.connect(action)
        QShortcut(QKeySequence("Shift+F10"), self.table).activated.connect(
            lambda: self._show_context_menu(
                self.table.visualRect(self.table.currentIndex()).center()
            )
        )

    # ---------------------------------------------------------------- #
    #  UI construction                                                   #
    # ---------------------------------------------------------------- #

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_content(), stretch=1)
        root.addLayout(body, stretch=1)

        root.addWidget(self._build_statusbar())

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(66)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)

        icon_label = QLabel()
        ico_path = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__))), "GuildBossToolkit.ico")
        icon_pix = QPixmap(ico_path).scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        icon_label.setPixmap(icon_pix)
        icon_label.setFixedSize(36, 36)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(icon_label)

        title = QLabel("GUILD TRACKER")
        title.setObjectName("app_title")
        h.addWidget(title)

        self.season_label = QLabel(self._current_season_name)
        self.season_label.setObjectName("season_label")
        h.addWidget(self.season_label)
        h.addStretch()

        self.count_label = QLabel("0 игроков")
        self.count_label.setObjectName("metric")
        h.addWidget(self.count_label)

        sep = QLabel("|")
        sep.setStyleSheet(style_for("color:#353840; font-size:16px;"))
        h.addWidget(sep)

        self.total_label = QLabel("Итоговый Счёт: —")
        self.total_label.setObjectName("metric")
        self.total_label.setToolTip("Сумма итоговых очков всех игроков гильдии")
        h.addWidget(self.total_label)

        return bar

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setMinimumWidth(round(265 * ui_scale()))
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 10, 12, 14)
        v.setSpacing(7)

        def section(text: str):
            label = QLabel(text)
            label.setObjectName("nav_label")
            v.addWidget(label)

        def action_btn(icon, text, slot) -> QPushButton:
            btn = QPushButton(f"{icon}  {text}")
            btn.setProperty("class", "action")
            btn.clicked.connect(slot)
            return btn

        section("ИСТОЧНИКИ ДАННЫХ")
        manual_btn = QPushButton("▣  Ручная обработка OCR")
        manual_btn.setObjectName("start_btn")
        manual_btn.clicked.connect(self._open_manual_mode)
        v.addWidget(manual_btn)

        network_btn = QPushButton("◇  Импорт из захвата данных")
        network_btn.setProperty("class", "action")
        network_btn.clicked.connect(self._open_network_capture)
        v.addWidget(network_btn)

        section("СИНХРОНИЗАЦИЯ")
        sheets_btn = QPushButton("↗  Экспорт в Google Таблицу")
        sheets_btn.setObjectName("sheets_btn")
        sheets_btn.clicked.connect(self._export_sheets)
        v.addWidget(sheets_btn)
        
        import_btn = QPushButton("↙  Импорт из Google Таблицы")
        import_btn.setObjectName("import_btn")
        import_btn.clicked.connect(self._import_sheets)
        v.addWidget(import_btn)

        section("УПРАВЛЕНИЕ")
        seasons_btn = QPushButton("▦  Сезоны")
        seasons_btn.setObjectName("seasons_btn")
        seasons_btn.clicked.connect(self._open_seasons)
        v.addWidget(seasons_btn)
        v.addWidget(action_btn("◇", "Калибровка OCR", self._open_badge_calibration))
        v.addWidget(action_btn("▤", "История изменений", self._open_history))
        v.addWidget(action_btn("↶", "Отменить последний импорт", self._undo_last_import))
        v.addWidget(action_btn("⚙", "Настройки", self._open_settings))

        section("ФАЙЛЫ")
        v.addWidget(action_btn("↗", "Экспорт CSV", self._export_csv))

        v.addStretch()

        return side

    def _build_content(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(style_for("QSplitter::handle { background:#22242c; height:2px; }"))

        # Top: data table
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)

        header = QWidget()
        header.setObjectName("content_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 20, 14)
        titles = QVBoxLayout()
        title = QLabel("ТАБЛИЦА ГИЛЬДИИ")
        title.setObjectName("content_title")
        titles.addWidget(title)
        hint = QLabel("Счёт игроков по дням сезона · двойной клик для правки")
        hint.setObjectName("content_hint")
        titles.addWidget(hint)
        header_layout.addLayout(titles)
        header_layout.addStretch()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Поиск игрока…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._filter_table)
        header_layout.addWidget(self.search)
        tv.addWidget(header)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximum(0)  # indeterminate
        tv.addWidget(self.progress)

        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "#", "Игрок",
            "День 1", "День 2", "День 3", "День 4", "День 5", "День 6", "День 7",
            "Итого"
        ])
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(COL_DAY1, COL_TOTAL + 1):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_POS, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        # Right-click context menu
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # Double-click for editing
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Tooltip for day columns
        for day in range(1, 8):
            item = self.table.horizontalHeaderItem(COL_DAY1 + day - 1)
            if item:
                item.setToolTip("Дневной прирост (дельта). Двойной клик — ввести прирост или суммарный счёт.")

        tv.addWidget(self.table, stretch=1)

        # Bottom: log
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)

        log_header = QLabel("  ЖУРНАЛ СОБЫТИЙ")
        log_header.setStyleSheet(style_for("background:#13141a; color:#8c8a7e; font-size:11px; font-weight:700; letter-spacing:1px; padding:7px 14px; border-bottom:1px solid #22242c;"))
        bv.addWidget(log_header)

        self.log = QTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        bv.addWidget(self.log, stretch=1)

        splitter.addWidget(top)
        splitter.addWidget(bottom)
        splitter.setSizes([450, 200])
        return splitter

    def _build_statusbar(self) -> QLabel:
        self.status_bar = QLabel("Готово · Выберите сезон и источник данных")
        self.status_bar.setObjectName("status_bar")
        self.status_bar.setFixedHeight(26)
        return self.status_bar

    # ---------------------------------------------------------------- #
    #  Actions                                                           #
    # ---------------------------------------------------------------- #

    def _auto_select_last_season(self):
        season = get_last_active_season()
        if season:
            name = season["name"] or f"Сезон {season['number']}"
            self._on_season_selected(season["id"], name)

    def _open_seasons(self):
        dlg = SeasonDialog(self)
        dlg.season_selected.connect(self._on_season_selected)
        dlg.exec()

    def _on_season_selected(self, season_id: int, name: str):
        self._current_season_id   = season_id
        self._current_season_name = name
        self.season_label.setText(name)
        self._load_table()
        self.status_bar.setText(f"Сезон выбран: {name}")

    def _open_manual_mode(self):
        if self._current_season_id is None:
            mb_warning(self, "Сезон не выбран",
                       "Сначала выберите или создайте сезон.")
            return
        dlg = ManualModeDialog(self._current_season_id, self._current_season_name, self)
        dlg.finished.connect(lambda _: self._load_table())
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _open_network_capture(self):
        if self._current_season_id is None:
            mb_warning(self, "Сезон не выбран", "Сначала выберите или создайте сезон.")
            return
        from ui.network_capture_wait_dialog import NetworkCaptureWaitDialog
        dialog = NetworkCaptureWaitDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.snapshot is not None:
            self._show_network_preview(dialog.snapshot)

    def _show_network_preview(self, snapshot):
        from ui.network_import_dialog import NetworkImportDialog
        dialog = NetworkImportDialog(
            snapshot, self._current_season_id, self._current_season_name, self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._load_table()
            self.status_bar.setText(f"Импортировано {len(snapshot.players)} игроков из сетевого захвата.")

    def _open_badge_calibration(self):
        dlg = BadgeOffsetCalibrationDialog(self)
        dlg.calibration_saved.connect(
            lambda: self.status_bar.setText("Калибровка бейджа сохранена."))
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _open_settings(self):
        SettingsDialog(self).exec()

    def _open_history(self):
        HistoryDialog(self._current_season_id, self).exec()

    def _undo_last_import(self):
        entry = latest_import()
        if entry is None:
            mb_info(self, "Откат импорта", "Нет импорта, который можно отменить.")
            return
        if not mb_question(
            self, "Откат импорта",
            f"Восстановить БД до последнего импорта ({entry['source']}, {entry['finished_at']})?\n"
            "Откат доступен, только если после импорта не было других изменений.",
        ):
            return
        try:
            restore_latest_import()
            init_db()
            self._load_table()
            self.status_bar.setText("Последний импорт отменён. Копия состояния до отката сохранена.")
        except Exception as exc:
            mb_warning(self, "Откат невозможен", str(exc))

    # ---------------------------------------------------------------- #
    #  Table                                                             #
    # ---------------------------------------------------------------- #

    def _load_table(self):
        if self._current_season_id is None:
            return
        rows = get_scores_for_season(self._current_season_id)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # Col 0: position (read-only)
            pos_item = QTableWidgetItem(str(r["position"] or ""))
            pos_item.setFlags(pos_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, COL_POS, pos_item)

            # Col 1: player name — store player_id in UserRole
            name_item = QTableWidgetItem(r["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, r["player_id"])
            if r.get("locked"):
                name_item.setForeground(theme_color(QColor("#e8b84b")))
                name_item.setToolTip("Имя заблокировано — не будет изменено OCR. ПКМ для разблокировки.")
            else:
                name_item.setToolTip("Двойной клик — переименовать. ПКМ — заблокировать.")
            self.table.setItem(row, COL_NAME, name_item)

            # Cols 2-8: daily delta scores
            for day in range(1, 8):
                col = COL_DAY1 + day - 1
                val = r["daily_scores"].get(day)
                snapshot = r["day_snapshots"].get(day)
                if val is not None:
                    item = QTableWidgetItem(f"{val:,}")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    # Store metadata for editing
                    item.setData(Qt.ItemDataRole.UserRole, {
                        "player_id": r["player_id"],
                        "day": day,
                        "snapshot": snapshot,
                    })
                    item.setToolTip(f"День {day}: прирост {val:,}\nСнапшот: {snapshot:,}\nДвойной клик — изменить.")
                else:
                    item = QTableWidgetItem("—")
                    item.setForeground(theme_color(QColor("#777267")))
                    item.setData(Qt.ItemDataRole.UserRole, {
                        "player_id": r["player_id"],
                        "day": day,
                        "snapshot": None,
                    })
                    item.setToolTip(f"День {day}: нет данных. Двойной клик — ввести.")
                self.table.setItem(row, col, item)

            # Col 9: total score (read-only, numeric sort)
            total_item = NumericItem(f"{r['total_score']:,}")
            total_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            total_item.setFlags(total_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, COL_TOTAL, total_item)

        self.table.setSortingEnabled(True)
        self.table.sortItems(COL_TOTAL, Qt.SortOrder.DescendingOrder)

        MEDAL = {
            0: ("#e8b84b", "#272016"),
            1: ("#c9c9c9", "#1d1f24"),
            2: ("#c79268", "#231d1a"),
        }
        for i in range(self.table.rowCount()):
            pos_item = self.table.item(i, COL_POS)
            if pos_item:
                pos_item.setText(str(i + 1))
                if i in MEDAL:
                    color, bg = MEDAL[i]
                    for c in range(self.table.columnCount()):
                        cell = self.table.item(i, c)
                        if cell:
                            cell.setBackground(theme_color(QColor(bg)))
                            if c != COL_NAME:
                                cell.setForeground(theme_color(QColor(color)))

        self.count_label.setText(f"{len(rows)} игроков")
        guild_total = sum(r["total_score"] for r in rows if r["total_score"])
        self.total_label.setText(f"Итоговый счёт: {guild_total:,}")
        self._filter_table(self.search.text())

    def _filter_table(self, query: str):
        query = query.strip().casefold()
        visible = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            hidden = bool(query) and (item is None or query not in item.text().casefold())
            self.table.setRowHidden(row, hidden)
            visible += not hidden
        total = self.table.rowCount()
        self.count_label.setText(f"{visible} из {total} игроков" if query else f"{total} игроков")

    # ---------------------------------------------------------------- #
    #  Editing (double-click)                                            #
    # ---------------------------------------------------------------- #

    def _on_item_double_clicked(self, item: QTableWidgetItem):
        col = item.column()
        row = item.row()

        if col == COL_NAME:
            self._edit_player_name(row, item)
        elif COL_DAY1 <= col <= COL_DAY7:
            self._edit_day_score(row, col, item)

    def _edit_player_name(self, row: int, item: QTableWidgetItem):
        player_id = item.data(Qt.ItemDataRole.UserRole)
        current_name = item.text()

        new_name, ok = ask_text(
            self,
            "Переименовать игрока",
            "Новое имя:",
            text=current_name,
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == current_name:
            return

        success = rename_player(player_id, new_name)
        if not success:
            mb_warning(self, "Ошибка",
                       f"Имя «{new_name}» уже занято другим игроком.")
            return
        self._load_table()

    def _edit_day_score(self, row: int, col: int, item: QTableWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return

        player_id        = data["player_id"]
        day_number       = data["day"]
        current_snapshot = data.get("snapshot") or 0

        # Previous day's snapshot — needed to convert delta ↔ snapshot
        prev_snapshot = 0
        if day_number > 1:
            prev_item = self.table.item(row, COL_DAY1 + day_number - 2)
            if prev_item:
                prev_data = prev_item.data(Qt.ItemDataRole.UserRole)
                if prev_data and prev_data.get("snapshot") is not None:
                    prev_snapshot = prev_data["snapshot"]

        name_item   = self.table.item(row, COL_NAME)
        player_name = name_item.text() if name_item else "?"

        current_delta = max(0, current_snapshot - prev_snapshot) if current_snapshot else 0

        # ── Dialog ────────────────────────────────────────────────────
        dlg = QDialog(self)
        dlg.setWindowTitle(f"День {day_number} — {player_name}")
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(style_for(DIALOG_STYLE + """
            QRadioButton { color: #ccc8bc; font-size: 13px; padding: 4px 0; }
            QRadioButton::indicator { width: 15px; height: 15px; }
            QLabel#hint { color: #8c8a7e; font-size: 12px; }
        """))
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        rb_delta    = QRadioButton("Дневной прирост (дельта)")
        rb_snapshot = QRadioButton("Суммарный счёт за день")
        rb_delta.setChecked(True)
        layout.addWidget(rb_delta)
        layout.addWidget(rb_snapshot)

        hint = QLabel(f"Прирост за день {day_number}:")
        hint.setObjectName("hint")
        layout.addWidget(hint)

        spin = AppSpinBox()
        spin.setRange(0, 999_999_999)
        spin.setValue(current_delta)
        layout.addWidget(spin)

        def _on_mode_toggled():
            if rb_delta.isChecked():
                hint.setText(f"Прирост за день {day_number}:")
                spin.setValue(max(0, spin.value() - prev_snapshot))
            else:
                hint.setText(f"Суммарный счёт за день {day_number} (накопленный total из игры):")
                spin.setValue(spin.value() + prev_snapshot)

        rb_delta.toggled.connect(_on_mode_toggled)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        val = spin.value()
        snapshot_to_save = (prev_snapshot + val) if rb_delta.isChecked() else val
        save_day_score(self._current_season_id, player_id, day_number, snapshot_to_save)
        self._load_table()

    # ---------------------------------------------------------------- #
    #  Context menu (right-click) — lock / unlock                       #
    # ---------------------------------------------------------------- #

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())

        menu = QMenu(self)
        menu.setStyleSheet(style_for(STYLESHEET))

        if row >= 0:
            name_item = self.table.item(row, COL_NAME)
            if name_item is not None:
                player_id = name_item.data(Qt.ItemDataRole.UserRole)
                is_locked = get_player_lock_state(player_id)

                if is_locked:
                    action = QAction("🔓  Разблокировать имя", self)
                    action.triggered.connect(lambda: self._toggle_lock(player_id, False))
                else:
                    action = QAction("🔒  Заблокировать имя", self)
                    action.triggered.connect(lambda: self._toggle_lock(player_id, True))
                menu.addAction(action)

                rename_action = QAction("✏  Переименовать", self)
                rename_action.triggered.connect(
                    lambda: self._edit_player_name(row, name_item)
                )
                menu.addAction(rename_action)

                delete_action = QAction("🗑  Удалить из сезона", self)
                delete_action.triggered.connect(
                    lambda: self._delete_player_manually(player_id, name_item.text())
                )
                menu.addAction(delete_action)
                menu.addSeparator()

        add_action = QAction("＋  Добавить игрока", self)
        add_action.triggered.connect(self._add_player_manually)
        menu.addAction(add_action)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _toggle_lock(self, player_id: int, lock: bool):
        set_player_locked(player_id, lock)
        state = "заблокировано" if lock else "разблокировано"
        self.status_bar.setText(f"Имя игрока {state}.")
        self._load_table()

    def _delete_player_manually(self, player_id: int, name: str):
        if not mb_question(
            self, "Удалить игрока",
            f"Удалить «{name}» из текущего сезона?\n\nВсе очки игрока за этот сезон будут удалены.",
        ):
            return
        remove_player_from_season(self._current_season_id, player_id)
        self.status_bar.setText(f"Игрок «{name}» удалён из сезона.")
        self._load_table()

    def _add_player_manually(self):
        if self._current_season_id is None:
            mb_warning(self, "Нет сезона", "Сначала выберите сезон.")
            return

        name, ok = ask_text(self, "Добавить игрока", "Имя игрока:")
        name = name.strip()
        if not ok or not name:
            return

        success, err = add_player_to_season(self._current_season_id, name)
        if not success:
            mb_warning(self, "Ошибка", err)
            return

        self.status_bar.setText(f"Игрок «{name}» добавлен.")
        self._load_table()

    # ---------------------------------------------------------------- #
    #  Export                                                            #
    # ---------------------------------------------------------------- #

    def _export_csv(self):
        if self._current_season_id is None:
            mb_warning(self, "Нет сезона", "Выберите сезон для экспорта.")
            return
        from PyQt6.QtWidgets import QFileDialog
        import csv
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить CSV", "", "CSV (*.csv)")
        if not path:
            return
        rows = get_scores_for_season(self._current_season_id)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Position", "Player",
                    "Day1", "Day2", "Day3", "Day4", "Day5", "Day6", "Day7",
                    "Total Score"
                ])
                for r in rows:
                    day_cols = [r["daily_scores"].get(d, "") for d in range(1, 8)]
                    # Replace None with empty string
                    day_cols = ["" if v is None else v for v in day_cols]
                    writer.writerow([r["position"], r["name"]] + day_cols + [r["total_score"]])
        except OSError as exc:
            mb_critical(self, "Ошибка экспорта CSV", str(exc))
            return
        self._log(f"📄 CSV экспортирован: {path}")
        self.status_bar.setText("Экспорт CSV завершён.")

    def _export_sheets(self):
        if self._sheets_busy():
            self.status_bar.setText("Обмен с Google Таблицей уже выполняется.")
            return
        from config.settings_manager import load_settings
        settings = load_settings()
        sheet_id = settings.get("google_sheets_id", "").strip()
        creds_path = settings.get("google_credentials_path", "").strip()

        if not sheet_id:
            mb_warning(self, "Google Sheets",
                       "Таблица не выбрана.\nОткройте Настройки и добавьте таблицу.")
            return
        if not creds_path:
            mb_warning(self, "Google Sheets",
                       "Не указан путь к credentials.json.\nОткройте Настройки.")
            return
        if self._current_season_id is None:
            mb_warning(self, "Google Sheets", "Выберите сезон для синхронизации.")
            return

        self._export_tabs = _SheetsImportTabsWorker(creds_path, sheet_id, self._current_season_id)
        self._export_tabs.finished.connect(self._on_export_tabs_ready)
        self._export_tabs.error.connect(self._on_sheets_error)
        self._export_tabs.start()
        self.status_bar.setText("Загрузка списка листов Google Таблицы…")

    def _on_export_tabs_ready(self, worksheets):
        from config.settings_manager import load_settings, save_settings
        from ui.sheets_tab_dialog import SheetsTabDialog

        worker = self._export_tabs
        settings = load_settings()
        saved_id = settings.get("google_worksheet_ids", {}).get(worker.sheet_id)
        dialog = SheetsTabDialog(worksheets, saved_id, worker.sheet_id, parent=self, exporting=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_bar.setText("Экспорт отменён.")
            return
        settings.setdefault("google_worksheet_ids", {})[worker.sheet_id] = dialog.worksheet_id
        save_settings(settings)
        self._sheets_fetch = _SheetsFetchWorker(
            worker.creds_path, worker.sheet_id, worker.season_id, dialog.worksheet_id)
        self._sheets_fetch.finished.connect(self._on_sheets_preview_ready)
        self._sheets_fetch.error.connect(self._on_sheets_error)
        self._sheets_fetch.start()
        self.status_bar.setText("Загрузка данных из Google Sheets…")

    def _on_sheets_preview_ready(self, preview):
        self.status_bar.setText("Данные загружены. Откройте предпросмотр.")
        from ui.sheets_preview_dialog import SheetsPreviewDialog
        dlg = SheetsPreviewDialog(preview, parent=self)
        if dlg.exec() != SheetsPreviewDialog.DialogCode.Accepted:
            self.status_bar.setText("Синхронизация отменена.")
            return
        self._sheets_write = _SheetsWriteWorker(preview)
        self._sheets_write.finished.connect(self._on_sheets_done)
        self._sheets_write.error.connect(self._on_sheets_error)
        self._sheets_write.start()
        self.status_bar.setText("Запись в Google Sheets…")

    def _on_sheets_done(self, result):
        self.status_bar.setText(f"Google Sheets: обновлено {result.updated} игроков.")
        self._log(f"Google Sheets синхронизация завершена. {result.summary()}")
        mb_info(self, "Google Sheets — готово", result.summary())

    def _on_sheets_error(self, message: str):
        self.status_bar.setText("Ошибка синхронизации Google Sheets.")
        self._log(f"[Ошибка] Google Sheets: {message}")
        mb_critical(self, "Google Sheets — ошибка", message)

    def _import_sheets(self):
        if self._sheets_busy():
            self.status_bar.setText("Обмен с Google Таблицей уже выполняется.")
            return
        from config.settings_manager import load_settings
        settings = load_settings()
        sheet_id = settings.get("google_sheets_id", "").strip()
        creds_path = settings.get("google_credentials_path", "").strip()

        if not sheet_id:
            mb_warning(self, "Импорт из Google Sheets",
                       "Таблица не выбрана.\nОткройте Настройки и добавьте таблицу.")
            return
        if not creds_path:
            mb_warning(self, "Импорт из Google Sheets",
                       "Не указан путь к credentials.json.\nОткройте Настройки.")
            return
        if self._current_season_id is None:
            mb_warning(self, "Импорт из Google Sheets", "Выберите сезон для импорта.")
            return

        self._import_tabs = _SheetsImportTabsWorker(
            creds_path, sheet_id, self._current_season_id
        )
        self._import_tabs.finished.connect(self._on_import_tabs_ready)
        self._import_tabs.error.connect(self._on_import_error)
        self._import_tabs.start()
        self.status_bar.setText("Загрузка списка листов Google Таблицы…")

    def _on_import_tabs_ready(self, worksheets):
        from config.settings_manager import load_settings
        from ui.sheets_tab_dialog import SheetsTabDialog

        worker = self._import_tabs
        settings = load_settings()
        saved_id = settings.get("google_worksheet_ids", {}).get(worker.sheet_id)
        name = next((entry.get("name") or worker.sheet_id
                     for entry in settings.get("google_sheets_history", [])
                     if entry.get("id") == worker.sheet_id), worker.sheet_id)
        dialog = SheetsTabDialog(worksheets, saved_id, name, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_bar.setText("Импорт отменён.")
            return

        self._import_fetch = _SheetsImportFetchWorker(
            worker.creds_path, worker.sheet_id, worker.season_id,
            dialog.worksheet_id,
        )
        self._import_fetch.finished.connect(self._on_import_preview_ready)
        self._import_fetch.error.connect(self._on_import_error)
        self._import_fetch.start()
        self.status_bar.setText(f"Загрузка листа «{dialog.combo.currentText()}»…")

    def _on_import_preview_ready(self, preview):
        from config.settings_manager import load_settings, save_settings
        settings = load_settings()
        settings.setdefault("google_worksheet_ids", {})[preview.sheet_id] = preview.worksheet_id
        save_settings(settings)
        self.status_bar.setText(f"Загружен лист «{preview.worksheet_title}».")
        from ui.sheets_import_preview_dialog import SheetsImportPreviewDialog
        dlg = SheetsImportPreviewDialog(preview, parent=self)
        if dlg.exec() != SheetsImportPreviewDialog.DialogCode.Accepted:
            self.status_bar.setText("Импорт отменён.")
            return
        self._import_write = _SheetsImportWriteWorker(preview)
        self._import_write.finished.connect(self._on_import_done)
        self._import_write.error.connect(self._on_import_error)
        self._import_write.start()
        self.status_bar.setText("Запись данных в БД…")

    def _on_import_done(self, result):
        self.status_bar.setText(f"Импорт завершён: {result.imported} игроков.")
        self._log(f"Импорт из Google Sheets завершён. {result.summary()}")
        mb_info(self, "Импорт завершён", result.summary())
        self._load_table()

    def _on_import_error(self, message: str):
        self.status_bar.setText("Ошибка импорта из Google Sheets.")
        self._log(f"[Ошибка] Импорт из Google Sheets: {message}")
        mb_critical(self, "Импорт — ошибка", message)

    # ---------------------------------------------------------------- #
    #  Log                                                               #
    # ---------------------------------------------------------------- #

    def _log(self, text: str):
        self.log.append(text)
