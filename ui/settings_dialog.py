from datetime import date

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QDoubleSpinBox, QSpinBox,
    QGroupBox, QFileDialog, QComboBox
)
from ui.style_utils import mb_warning, mb_info, mb_question, ask_text
from PyQt6.QtCore import Qt
from config.settings_manager import load_settings, save_settings

STYLESHEET = """
QDialog { background: #0a0e17; color: #d0ddf0; font-family: 'Segoe UI', sans-serif; }
QGroupBox { border: 1px solid #2a3a5a; border-radius:6px; margin-top:10px;
            color:#8aabcf; font-size:12px; padding:10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QLabel { color:#c8d8f0; font-size:13px; }
QLabel#title { font-size:18px; font-weight:bold; color:#f0c040; padding:8px 0; }
QLineEdit, QDoubleSpinBox, QSpinBox {
    background:#1a2035; color:#d0ddf0; border:1px solid #2a3a5a;
    border-radius:4px; padding:5px 8px; font-size:13px; }
QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right;
    width:18px; border-left:1px solid #2a3a5a; border-bottom:1px solid #2a3a5a;
    border-top-right-radius:4px; background:#1e2840; }
QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover { background:#2a3a5a; }
QDoubleSpinBox::up-button:pressed, QSpinBox::up-button:pressed { background:#3a4a6a; }
QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right;
    width:18px; border-left:1px solid #2a3a5a; border-top:1px solid #2a3a5a;
    border-bottom-right-radius:4px; background:#1e2840; }
QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover { background:#2a3a5a; }
QDoubleSpinBox::down-button:pressed, QSpinBox::down-button:pressed { background:#3a4a6a; }
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {
    image: none; width:0; height:0;
    border-left:4px solid transparent; border-right:4px solid transparent;
    border-bottom:5px solid #8aabcf; }
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {
    image: none; width:0; height:0;
    border-left:4px solid transparent; border-right:4px solid transparent;
    border-top:5px solid #8aabcf; }
QComboBox {
    background:#1a2035; color:#d0ddf0; border:1px solid #2a3a5a;
    border-radius:4px; padding:5px 8px; font-size:10pt; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background:#1a2035; color:#d0ddf0; selection-background-color:#2a3a5a;
    font-size:10pt; }
QPushButton {
    background:#1e2840; color:#c8d8f0; border:1px solid #2a3a5a;
    border-radius:6px; padding:7px 16px; font-size:13px; }
QPushButton:hover { background:#2a3a5a; }
QPushButton#primary { background:#1a4a7a; border-color:#3a7abf; color:#fff; font-weight:bold; }
QPushButton#primary:hover { background:#2a5a9a; }
QPushButton#danger { background:#3a1a1a; border-color:#7a2a2a; color:#f08080; }
QPushButton#danger:hover { background:#4a2a2a; }
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки — Guild Tracker")
        self.setStyleSheet(STYLESHEET)
        self.setMinimumWidth(520)
        self._settings = load_settings()
        # Work on a mutable copy of history so we can add/remove without saving immediately
        self._history: list[dict] = list(self._settings.get("google_sheets_history", []))
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Настройки")
        title.setObjectName("title")
        layout.addWidget(title)

        # Capture group
        cap_group = QGroupBox("Захват экрана")
        cap_layout = QVBoxLayout(cap_group)

        pause_row = QHBoxLayout()
        pause_row.addWidget(QLabel("Пауза между скроллами (сек):"))
        self.pause_spin = QDoubleSpinBox()
        self.pause_spin.setRange(0.3, 10.0)
        self.pause_spin.setSingleStep(0.1)
        self.pause_spin.setValue(self._settings.get("scroll_pause", 1.0))
        pause_row.addWidget(self.pause_spin)
        cap_layout.addLayout(pause_row)

        scroll_row = QHBoxLayout()
        scroll_row.addWidget(QLabel("Скролл (строк за шаг):"))
        self.scroll_spin = QSpinBox()
        self.scroll_spin.setRange(1, 20)
        self.scroll_spin.setValue(self._settings.get("scroll_amount", 3))
        scroll_row.addWidget(self.scroll_spin)
        cap_layout.addLayout(scroll_row)

        ticks_row = QHBoxLayout()
        ticks_row.addWidget(QLabel("Тиков на строку (подбери если скролл не работает):"))
        self.ticks_spin = QSpinBox()
        self.ticks_spin.setRange(1, 30)
        self.ticks_spin.setValue(self._settings.get("ticks_per_row", 5))
        ticks_row.addWidget(self.ticks_spin)
        cap_layout.addLayout(ticks_row)

        countdown_row = QHBoxLayout()
        countdown_row.addWidget(QLabel("Обратный отсчёт перед стартом (сек):"))
        self.countdown_spin = QSpinBox()
        self.countdown_spin.setRange(1, 30)
        self.countdown_spin.setValue(self._settings.get("countdown_seconds", 5))
        countdown_row.addWidget(self.countdown_spin)
        cap_layout.addLayout(countdown_row)

        from PyQt6.QtWidgets import QCheckBox
        self.debug_crops_cb = QCheckBox("Сохранять кропы каждого скриншота в debug_crops/")
        self.debug_crops_cb.setStyleSheet("color:#8aabcf; font-size:12px;")
        self.debug_crops_cb.setChecked(self._settings.get("debug_save_crops", False))
        cap_layout.addWidget(self.debug_crops_cb)

        layout.addWidget(cap_group)

        # Database group
        db_group = QGroupBox("База данных")
        db_layout = QHBoxLayout(db_group)
        self.db_path_edit = QLineEdit(self._settings.get("db_path", ""))
        browse_btn = QPushButton("Обзор…")
        browse_btn.clicked.connect(self._browse_db)
        db_layout.addWidget(self.db_path_edit, stretch=1)
        db_layout.addWidget(browse_btn)
        layout.addWidget(db_group)

        # Google Sheets group
        gs_group = QGroupBox("Google Sheets (опционально)")
        gs_layout = QVBoxLayout(gs_group)

        # Sheet selector row
        gs_layout.addWidget(QLabel("Таблица:"))
        sheet_row = QHBoxLayout()
        self.gs_combo = QComboBox()
        self.gs_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._populate_combo()
        sheet_row.addWidget(self.gs_combo, stretch=1)

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(32)
        add_btn.setToolTip("Добавить таблицу по ID")
        add_btn.clicked.connect(self._add_sheet)
        sheet_row.addWidget(add_btn)

        del_btn = QPushButton("×")
        del_btn.setFixedWidth(32)
        del_btn.setObjectName("danger")
        del_btn.setToolTip("Удалить выбранную таблицу из истории")
        del_btn.clicked.connect(self._remove_sheet)
        sheet_row.addWidget(del_btn)

        gs_layout.addLayout(sheet_row)

        # Credentials
        gs_layout.addWidget(QLabel("Путь к credentials.json:"))
        cred_row = QHBoxLayout()
        self.cred_edit = QLineEdit(self._settings.get("google_credentials_path", ""))
        cred_browse = QPushButton("Обзор…")
        cred_browse.clicked.connect(self._browse_cred)
        cred_row.addWidget(self.cred_edit, stretch=1)
        cred_row.addWidget(cred_browse)
        gs_layout.addLayout(cred_row)

        layout.addWidget(gs_group)

        layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Сохранить")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Combo helpers
    # ------------------------------------------------------------------

    def _populate_combo(self):
        self.gs_combo.clear()
        self.gs_combo.addItem("— не выбрано —", userData="")
        current_id = self._settings.get("google_sheets_id", "")
        select_idx = 0
        for i, entry in enumerate(self._history, start=1):
            label = entry.get("name") or entry.get("id", "")
            self.gs_combo.addItem(label, userData=entry.get("id", ""))
            if entry.get("id") == current_id:
                select_idx = i
        self.gs_combo.setCurrentIndex(select_idx)

    def _selected_sheet_id(self) -> str:
        return self.gs_combo.currentData() or ""

    def _add_sheet(self):
        sheet_id, ok = ask_text(
            self, "Добавить таблицу",
            "Вставьте ID таблицы Google Sheets:",
        )
        if not ok or not sheet_id.strip():
            return
        sheet_id = sheet_id.strip()

        # Check for duplicates
        for entry in self._history:
            if entry.get("id") == sheet_id:
                mb_info(self, "Уже добавлено", "Эта таблица уже есть в истории.")
                # Select it in the combo
                for i in range(self.gs_combo.count()):
                    if self.gs_combo.itemData(i) == sheet_id:
                        self.gs_combo.setCurrentIndex(i)
                        break
                return

        # Try to fetch name via API if credentials are available
        name = sheet_id
        creds_path = self.cred_edit.text().strip()
        if creds_path:
            try:
                from core.sheets_sync import get_spreadsheet_name
                name = get_spreadsheet_name(creds_path, sheet_id)
            except Exception as e:
                mb_warning(
                    self, "Не удалось получить имя",
                    f"Таблица добавлена по ID (имя не загружено).\n{e}"
                )

        entry = {"id": sheet_id, "name": name, "last_used": str(date.today())}
        self._history.append(entry)
        self._populate_combo()
        # Select the newly added entry
        for i in range(self.gs_combo.count()):
            if self.gs_combo.itemData(i) == sheet_id:
                self.gs_combo.setCurrentIndex(i)
                break

    def _remove_sheet(self):
        idx = self.gs_combo.currentIndex()
        if idx <= 0:  # "— не выбрано —" or nothing
            return
        name = self.gs_combo.currentText()
        if not mb_question(self, "Удалить из истории",
                           f"Удалить «{name}» из истории таблиц?"):
            return
        sheet_id = self.gs_combo.currentData()
        self._history = [e for e in self._history if e.get("id") != sheet_id]
        self._populate_combo()

    # ------------------------------------------------------------------
    # File dialogs
    # ------------------------------------------------------------------

    def _browse_db(self):
        path, _ = QFileDialog.getSaveFileName(self, "Выберите файл БД", "", "SQLite (*.db);;All files (*)")
        if path:
            self.db_path_edit.setText(path)

    def _browse_cred(self):
        path, _ = QFileDialog.getOpenFileName(self, "credentials.json", "", "JSON (*.json)")
        if path:
            self.cred_edit.setText(path)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self):
        self._settings["scroll_pause"]            = self.pause_spin.value()
        self._settings["scroll_amount"]           = self.scroll_spin.value()
        self._settings["ticks_per_row"]           = self.ticks_spin.value()
        self._settings["countdown_seconds"]       = self.countdown_spin.value()
        self._settings["debug_save_crops"]        = self.debug_crops_cb.isChecked()
        self._settings["db_path"]                 = self.db_path_edit.text()
        self._settings["google_sheets_id"]        = self._selected_sheet_id()
        self._settings["google_credentials_path"] = self.cred_edit.text()
        self._settings["google_sheets_history"]   = self._history
        # Update last_used for the selected sheet
        selected_id = self._selected_sheet_id()
        if selected_id:
            for entry in self._settings["google_sheets_history"]:
                if entry.get("id") == selected_id:
                    entry["last_used"] = str(date.today())
                    break
        save_settings(self._settings)
        mb_info(self, "Сохранено", "Настройки сохранены.")
        self.accept()
