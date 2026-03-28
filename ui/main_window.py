"""Main application window."""

from __future__ import annotations
import os
import time
import pyautogui
from datetime import date as date_type
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QSplitter, QHeaderView, QAbstractItemView,
    QProgressBar, QMenu,
    QDialog, QRadioButton, QDialogButtonBox, QSpinBox,
)
from ui.style_utils import mb_warning, mb_critical, mb_info, mb_question, ask_text, ask_int
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QFont, QIcon, QAction, QPixmap

import pyautogui
from db.database import (
    init_db, get_scores_for_season, save_score, get_season,
    rename_player, set_player_locked, get_player_lock_state,
    save_day_score, get_all_player_names_with_lock, get_last_active_season,
    add_player_to_season, remove_player_from_season
)
from config.settings_manager import load_settings, get_roi_for_resolution
from ui.calibration import CalibrationDialog
from ui.settings_dialog import SettingsDialog
from ui.season_dialog import SeasonDialog
from ui.manual_mode_dialog import ManualModeDialog
from ui.badge_calibration import BadgeOffsetCalibrationDialog


# ------------------------------------------------------------------ #
#  Worker thread                                                       #
# ------------------------------------------------------------------ #

class _SheetsFetchWorker(QThread):
    """Phase 1: reads the sheet, builds diff — no writes."""
    finished = pyqtSignal(object)   # SyncPreview
    error    = pyqtSignal(str)

    def __init__(self, creds_path: str, sheet_id: str, season_id: int):
        super().__init__()
        self.creds_path = creds_path
        self.sheet_id   = sheet_id
        self.season_id  = season_id

    def run(self):
        try:
            from core.sheets_sync import prepare_sync
            preview = prepare_sync(self.creds_path, self.sheet_id, self.season_id)
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

    def __init__(self, creds_path: str, sheet_id: str, season_id: int):
        super().__init__()
        self.creds_path = creds_path
        self.sheet_id   = sheet_id
        self.season_id  = season_id

    def run(self):
        try:
            from core.sheets_sync import prepare_reverse_sync
            preview = prepare_reverse_sync(self.creds_path, self.sheet_id, self.season_id)
            self.finished.emit(preview)
        except Exception as e:
            self.error.emit(str(e))


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


class CollectWorker(QObject):
    player_found = pyqtSignal(int, str, int)
    log_message  = pyqtSignal(str)
    finished     = pyqtSignal(int)
    error        = pyqtSignal(str)

    def __init__(self, roi, settings, season_id, day_number: int = 1):
        super().__init__()
        self.roi        = roi
        self.settings   = settings
        self.season_id  = season_id
        self.day_number = day_number
        self._running   = False

    def stop(self):
        self._running = False

    def run(self):
        try:
            from core.ocr import extract_row_data
            from core.capture import scroll_down
        except ImportError as e:
            self.error.emit(f"Ошибка импорта: {e}")
            return

        self._running = True
        screen_w, screen_h = pyautogui.size()
        scroll_pause  = self.settings.get("scroll_pause", 1.2)
        scroll_amount = self.settings.get("scroll_amount", 3)
        ticks_per_row = self.settings.get("ticks_per_row", 5)
        save_debug    = self.settings.get("debug_save_crops", False)

        collected: dict[int, tuple[str, int]] = {}
        last_max = -1
        stall    = 0
        MAX_STALL = 3
        shot_idx  = 0

        # Pre-fetch player list once for batch fuzzy matching
        existing_players = get_all_player_names_with_lock()

        self.log_message.emit("▶ Начинаю сбор данных…")
        self.log_message.emit(f"  Экран: {screen_w}x{screen_h} | "
                              f"scroll: {scroll_amount} строк × {ticks_per_row} тиков | "
                              f"пауза: {scroll_pause}s | День {self.day_number}")
        self.log_message.emit(f"  Строк в ROI: {len(self.roi.get('rows', []))}")

        while self._running:
            try:
                screenshot = pyautogui.screenshot()
                rows = extract_row_data(
                    screenshot, self.roi, screen_w, screen_h,
                    log=self.log_message.emit,
                    save_debug=save_debug,
                    screenshot_index=shot_idx,
                )
                shot_idx += 1
            except Exception as e:
                self.error.emit(str(e))
                break

            new_this_frame = 0
            for pos, name, score in rows:
                if pos not in collected:
                    collected[pos] = (name, score)
                    new_this_frame += 1
                    save_score(self.season_id, name, score, pos,
                               day_number=self.day_number,
                               existing_players=existing_players)
                    self.player_found.emit(pos, name, score)

            if new_this_frame == 0 and rows:
                self.log_message.emit(f"  (все {len(rows)} игроков уже в базе — дубли)")

            cur_max = max(collected.keys()) if collected else 0
            if cur_max == last_max:
                stall += 1
                self.log_message.emit(f"  Нет новых игроков ({stall}/{MAX_STALL})…")
                if stall >= MAX_STALL:
                    self.log_message.emit("✅ Таблица закончилась.")
                    break
            else:
                stall    = 0
                last_max = cur_max

            scroll_down(scroll_amount, ticks_per_row)
            time.sleep(scroll_pause)

        self._running = False
        self.finished.emit(len(collected))


# ------------------------------------------------------------------ #
#  Stylesheet                                                          #
# ------------------------------------------------------------------ #

STYLESHEET = """
QMainWindow, QWidget#central {
    background: #0a0e17;
    color: #d0ddf0;
    font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
}
/* Top bar */
QWidget#topbar { background: #0d1220; border-bottom: 1px solid #1e2a40; }
QLabel#app_title {
    font-size: 22px;
    font-weight: bold;
    color: #f0c040;
    letter-spacing: 2px;
    padding: 0 8px;
}
QLabel#season_label {
    font-size: 13px;
    color: #6a8aaf;
    padding: 0 8px;
}
/* Sidebar */
QWidget#sidebar { background: #0d1220; border-right: 1px solid #1e2a40; min-width: 200px; max-width:220px; }
/* Action buttons */
QPushButton.action {
    background: #111c30;
    color: #a8c0e0;
    border: 1px solid #1e2a45;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    text-align: left;
}
QPushButton.action:hover  { background: #1a2a45; color:#ffffff; }
QPushButton.action:pressed{ background: #2a3a5a; }
QPushButton#start_btn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #1a4a2a, stop:1 #0a3a5a);
    color: #80ff80;
    border: 1px solid #2a6a3a;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 14px;
    font-weight: bold;
}
QPushButton#start_btn:hover   { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #206030,stop:1 #0a4a70); }
QPushButton#fc_btn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #3a2a1a, stop:1 #1a2a4a);
    color: #f0c060;
    border: 1px solid #6a4a2a;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 14px;
    font-weight: bold;
}
QPushButton#fc_btn:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4a3a2a,stop:1 #2a3a5a); }
QPushButton#sheets_btn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0a2a4a, stop:1 #1a3a2a);
    color: #60c8f0;
    border: 1px solid #2a6a8a;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    font-weight: bold;
    text-align: center;
}
QPushButton#sheets_btn:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0e3a6a,stop:1 #1a4a2a); }
QPushButton#import_btn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0a2a0a, stop:1 #0a2a3a);
    color: #80d080;
    border: 1px solid #2a6a3a;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    font-weight: bold;
    text-align: center;
}
QPushButton#import_btn:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0d3a0d,stop:1 #0a3a5a); }
QPushButton#seasons_btn {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2a1a4a, stop:1 #1a2a4a);
    color: #c090f0;
    border: 1px solid #5a3a8a;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 14px;
    font-weight: bold;
}
QPushButton#seasons_btn:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3a2a5a,stop:1 #2a3a5a); }
QPushButton#stop_btn {
    background: #3a1a1a;
    color: #ff8080;
    border: 1px solid #6a2a2a;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 14px;
    font-weight: bold;
}
QPushButton#stop_btn:hover { background: #4a2020; }
/* Status bar area */
QLabel#status_bar {
    background: #0d1220;
    border-top: 1px solid #1e2a40;
    color: #5a7a9a;
    font-size: 12px;
    padding: 4px 12px;
}
/* Table */
QTableWidget {
    background: #0d1625;
    color: #c0d4f0;
    gridline-color: #162030;
    border: none;
    font-size: 15px;
    font-family: 'Comic Sans MS', 'Segoe UI', sans-serif;
    selection-background-color: #1a3050;
}
QHeaderView::section {
    background: #0d1a2a;
    color: #7090b0;
    border: none;
    border-bottom: 1px solid #1e2a40;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 1px;
    text-transform: uppercase;
}
QTableWidget::item { padding: 4px 10px; border-bottom: 1px solid #111a27; }
QTableWidget::item:selected { background: #1a3050; color: #e0f0ff; }
/* Log */
QTextEdit#log {
    background: #080e18;
    color: #4a8a4a;
    border: none;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    padding: 8px;
}
/* Progress */
QProgressBar {
    background: #111a28;
    border: 1px solid #1e2a40;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}
QProgressBar::chunk { background: #2a7a4a; border-radius: 4px; }
/* Countdown */
QLabel#countdown {
    font-size: 48px;
    font-weight: bold;
    color: #f0c040;
    qproperty-alignment: AlignCenter;
}
/* Context menu */
QMenu {
    background: #1a2435;
    color: #c8d8f0;
    border: 1px solid #2a3a5a;
    border-radius: 4px;
    font-size: 13px;
}
QMenu::item { padding: 6px 20px; }
QMenu::item:selected { background: #2a3a5a; }
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
        self.setStyleSheet(STYLESHEET)

        self._current_season_id:   int | None = None
        self._current_season_name: str        = "Сезон не выбран"
        self._current_day:         int        = 1
        self._thread:   QThread | None        = None
        self._worker:   CollectWorker | None  = None
        self._countdown_val = 0

        init_db()
        self._build_ui()
        self._auto_select_last_season()

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
        bar.setFixedHeight(56)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)

        icon_label = QLabel()
        ico_path = os.path.join(os.path.dirname(__file__), "..", "GuildBossToolkit.ico")
        icon_pix = QPixmap(ico_path).scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio,
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
        self.count_label.setStyleSheet("color:#4a6a8a; font-size:12px; padding:0 8px;")
        h.addWidget(self.count_label)

        sep = QLabel("|")
        sep.setStyleSheet("color:#1e2a40; font-size:16px;")
        h.addWidget(sep)

        self.total_label = QLabel("Итоговый Счёт: —")
        self.total_label.setStyleSheet(
            "color:#f0c040; font-size:13px; font-weight:bold; padding:0 8px;"
        )
        self.total_label.setToolTip("Сумма итоговых очков всех игроков гильдии")
        h.addWidget(self.total_label)

        return bar

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 16, 12, 16)
        v.setSpacing(8)

        def action_btn(icon, text, slot) -> QPushButton:
            btn = QPushButton(f"{icon}  {text}")
            btn.setProperty("class", "action")
            btn.clicked.connect(slot)
            return btn

        self.stop_btn = QPushButton("■  Остановить")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop_collection)
        v.addWidget(self.stop_btn)

        manual_btn = QPushButton("⚔️  Обработка данных\nGS")
        manual_btn.setObjectName("start_btn")
        manual_btn.clicked.connect(self._open_manual_mode)
        v.addWidget(manual_btn)

        fc_btn = QPushButton("🎲  Обработка данных\nFC")
        fc_btn.setObjectName("fc_btn")
        fc_btn.clicked.connect(self._open_fc_mode)
        v.addWidget(fc_btn)

        v.addSpacing(8)

        sheets_btn = QPushButton("📊  Синхронизация с\nGoogle Таблицей")
        sheets_btn.setObjectName("sheets_btn")
        sheets_btn.clicked.connect(self._export_sheets)
        v.addWidget(sheets_btn)
        
        v.addSpacing(8)

        import_btn = QPushButton("📥  Импорт из\nGoogle Таблицы")
        import_btn.setObjectName("import_btn")
        import_btn.clicked.connect(self._import_sheets)
        v.addWidget(import_btn)

        v.addSpacing(8)

        seasons_btn = QPushButton("📅  Сезоны")
        seasons_btn.setObjectName("seasons_btn")
        seasons_btn.clicked.connect(self._open_seasons)
        v.addWidget(seasons_btn)
        calib_auto_btn = action_btn("🔧", "Калибровка авто", self._open_calibration)
        calib_auto_btn.setVisible(False)
        v.addWidget(calib_auto_btn)
        v.addWidget(action_btn("🎯", "Калибровка бейджа", self._open_badge_calibration))
        v.addWidget(action_btn("⚙", "Настройки",       self._open_settings))

        v.addSpacing(8)
        v.addWidget(action_btn("📄", "Экспорт CSV",  self._export_csv))

        v.addStretch()

        return side

    def _build_content(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet("QSplitter::handle { background:#1e2a40; height:2px; }")

        # Top: data table
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)

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
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in range(COL_DAY1, COL_TOTAL + 1):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_POS, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
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

        log_header = QLabel("  ◈ Журнал")
        log_header.setStyleSheet("background:#0f1520; color:#3a5a7a; font-size:12px; padding:4px 8px; border-bottom:1px solid #1e2a40;")
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
        self.status_bar = QLabel("Готов  |  Выберите сезон и нажмите «Начать сбор»")
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

    def _open_fc_mode(self):
        if self._current_season_id is None:
            mb_warning(self, "Сезон не выбран",
                       "Сначала выберите или создайте сезон.")
            return
        dlg = ManualModeDialog(self._current_season_id, self._current_season_name, self, mode="fc")
        dlg.finished.connect(lambda _: self._load_table())
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _open_calibration(self):
        dlg = CalibrationDialog(self)
        dlg.calibration_saved.connect(lambda: self.status_bar.setText("Калибровка сохранена."))
        dlg.exec()

    def _open_badge_calibration(self):
        dlg = BadgeOffsetCalibrationDialog(self)
        dlg.calibration_saved.connect(
            lambda: self.status_bar.setText("Калибровка бейджа сохранена."))
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()

    def _open_settings(self):
        SettingsDialog(self).exec()

    def _start_collection(self):
        if self._current_season_id is None:
            mb_warning(self, "Сезон не выбран", "Сначала выберите или создайте сезон.")
            return

        sw, sh = pyautogui.size()
        roi = get_roi_for_resolution(sw, sh)
        if roi is None:
            if mb_question(self, "Нет калибровки",
                           f"Калибровка для {sw}×{sh} не найдена.\nОткрыть окно калибровки?"):
                self._open_calibration()
            return

        day, ok = self._ask_day_number()
        if not ok:
            return
        self._current_day = day

        settings = load_settings()
        countdown = settings.get("countdown_seconds", 5)
        self._start_countdown(countdown, roi, settings)

    def _ask_day_number(self) -> tuple[int, bool]:
        """Show a dialog asking which day (1-7) to record. Auto-suggests from season dates."""
        suggested = 1
        if self._current_season_id is not None:
            season = get_season(self._current_season_id)
            if season and season.get("start_date"):
                try:
                    from datetime import datetime
                    start = datetime.strptime(season["start_date"], "%Y-%m-%d").date()
                    delta = (date_type.today() - start).days + 1
                    suggested = max(1, min(7, delta))
                except Exception:
                    pass

        val, ok = ask_int(
            self,
            "Выбор дня",
            f"Укажите день сезона (1–7):\n(Авто-подсказка: День {suggested})",
            value=suggested,
            min_val=1,
            max_val=7,
        )
        return val, ok

    def _start_countdown(self, n: int, roi, settings):
        self.start_btn.setVisible(False)
        self.stop_btn.setVisible(True)
        self.progress.setVisible(True)
        self._log(f"Переключитесь в игру! Начало через {n} секунд… (День {self._current_day})")
        self.status_bar.setText(f"Обратный отсчёт: {n}…")

        self._countdown_val = n
        self._roi_pending = roi
        self._settings_pending = settings
        self._cd_timer = QTimer(self)
        self._cd_timer.timeout.connect(self._tick_countdown)
        self._cd_timer.start(1000)

    def _tick_countdown(self):
        self._countdown_val -= 1
        if self._countdown_val <= 0:
            self._cd_timer.stop()
            self.status_bar.setText("Сбор данных…")
            self._run_worker(self._roi_pending, self._settings_pending)
        else:
            self._log(f"  {self._countdown_val}…")
            self.status_bar.setText(f"Обратный отсчёт: {self._countdown_val}…")

    def _run_worker(self, roi, settings):
        self._worker = CollectWorker(roi, settings, self._current_season_id, self._current_day)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.player_found.connect(self._on_player_found)
        self._worker.log_message.connect(self._log)
        self._worker.finished.connect(self._on_collection_finished)
        self._worker.error.connect(self._on_collection_error)

        self._thread.start()

    def _stop_collection(self):
        if self._worker:
            self._worker.stop()
        if hasattr(self, "_cd_timer"):
            self._cd_timer.stop()
        self._reset_controls()

    def _on_player_found(self, pos: int, name: str, score: int):
        """Add a row during live collection (day data populated on finish)."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        pos_item = QTableWidgetItem(str(pos))
        pos_item.setFlags(pos_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, COL_POS, pos_item)

        name_item = QTableWidgetItem(name)
        self.table.setItem(row, COL_NAME, name_item)

        # Day columns — blank during live collection
        for col in range(COL_DAY1, COL_TOTAL):
            self.table.setItem(row, col, QTableWidgetItem(""))

        score_item = NumericItem(f"{score:,}")
        score_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, COL_TOTAL, score_item)

        self.table.scrollToBottom()
        self.count_label.setText(f"{self.table.rowCount()} игроков")
        guild_total = sum(
            int(self.table.item(r, COL_TOTAL).text().replace(",", ""))
            for r in range(self.table.rowCount())
            if self.table.item(r, COL_TOTAL) and self.table.item(r, COL_TOTAL).text()
        )
        self.total_label.setText(f"Итоговый Счёт: {guild_total}")

    def _on_collection_finished(self, total: int):
        self._reset_controls()
        self._log(f"✅ Готово! Собрано игроков: {total}")
        self.status_bar.setText(f"Сбор завершён — {total} игроков")
        self._load_table()  # Reload to show day deltas

    def _on_collection_error(self, msg: str):
        self._reset_controls()
        self._log(f"❌ Ошибка: {msg}")
        mb_critical(self, "Ошибка сбора", msg)

    def _reset_controls(self):
        self.start_btn.setVisible(True)
        self.stop_btn.setVisible(False)
        self.progress.setVisible(False)
        if self._thread:
            self._thread.quit()
            self._thread.wait()

    # ---------------------------------------------------------------- #
    #  Table                                                             #
    # ---------------------------------------------------------------- #

    def _load_table(self):
        if self._current_season_id is None:
            return
        rows = get_scores_for_season(self._current_season_id)
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
                name_item.setForeground(QColor("#f0c040"))
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
                    item.setForeground(QColor("#3a5a7a"))
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
            0: ("#ffe066", "#2a2a0f"),
            1: ("#7a7aa9", "#6f6fee"),
            2: ("#e0a070", "#2a1a0f"),
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
                            cell.setBackground(QColor(bg))
                            if c != COL_NAME:
                                cell.setForeground(QColor(color))

        self.count_label.setText(f"{len(rows)} игроков")
        guild_total = sum(r["total_score"] for r in rows if r["total_score"])
        self.total_label.setText(f"Итоговый Счёт: {guild_total}")

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
        dlg.setStyleSheet(STYLESHEET + """
            QDialog { background: #0f1520; }
            QRadioButton { color: #c0d4f0; font-size: 13px; padding: 4px 0; }
            QRadioButton::indicator { width: 15px; height: 15px; }
            QLabel#hint { color: #6a8aaf; font-size: 12px; }
        """)
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

        spin = QSpinBox()
        spin.setRange(0, 999_999_999)
        spin.setValue(current_delta)
        spin.setStyleSheet("""
            QSpinBox {
                background: #111c30; color: #d0ddf0;
                border: 1px solid #2a3a5a; border-radius: 4px;
                padding: 4px 8px; font-size: 14px;
            }
            QSpinBox::up-button {
                subcontrol-origin: border; subcontrol-position: top right;
                width: 20px; border-left: 1px solid #2a3a5a;
                border-bottom: 1px solid #2a3a5a; background: #1a2a40;
            }
            QSpinBox::down-button {
                subcontrol-origin: border; subcontrol-position: bottom right;
                width: 20px; border-left: 1px solid #2a3a5a; background: #1a2a40;
            }
            QSpinBox::up-arrow {
                width: 0; height: 0;
                border-left: 4px solid transparent; border-right: 4px solid transparent;
                border-bottom: 5px solid #7090b0;
            }
            QSpinBox::down-arrow {
                width: 0; height: 0;
                border-left: 4px solid transparent; border-right: 4px solid transparent;
                border-top: 5px solid #7090b0;
            }
        """)
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
        menu.setStyleSheet(STYLESHEET)

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
        self._log(f"📄 CSV экспортирован: {path}")
        self.status_bar.setText("Экспорт CSV завершён.")

    def _export_sheets(self):
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

        self._sheets_fetch = _SheetsFetchWorker(creds_path, sheet_id, self._current_season_id)
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

        if not mb_question(
            self, "Импорт из Google Sheets",
            "Данные из Google Sheets будут записаны в локальную БД.\n"
            "Существующие данные за те же дни будут перезаписаны.\n\n"
            "Продолжить?"
        ):
            return

        self._import_fetch = _SheetsImportFetchWorker(
            creds_path, sheet_id, self._current_season_id
        )
        self._import_fetch.finished.connect(self._on_import_preview_ready)
        self._import_fetch.error.connect(self._on_import_error)
        self._import_fetch.start()
        self.status_bar.setText("Загрузка данных из Google Sheets для импорта…")

    def _on_import_preview_ready(self, preview):
        self.status_bar.setText("Данные загружены. Откройте предпросмотр импорта.")
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
