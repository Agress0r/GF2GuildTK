from ui.controls import AppDoubleSpinBox
from datetime import date

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QCheckBox, QApplication, QGroupBox, QFileDialog, QComboBox
from ui.style_utils import mb_warning, mb_info, mb_question, ask_text
from ui.theme import DIALOG_STYLE, style_for
from ui.controls import AppComboBox, ui_scale
from config.settings_manager import load_settings, save_settings

STYLESHEET = """
QGroupBox { border: 1px solid #353840; border-radius:6px; margin-top:10px;
            color:#a4a094; font-size:12px; padding:10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки — Guild Tracker")
        self.setStyleSheet(style_for(DIALOG_STYLE + STYLESHEET))
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
        self.gs_combo = AppComboBox()
        self.gs_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._populate_combo()
        sheet_row.addWidget(self.gs_combo, stretch=1)

        add_btn = QPushButton("+")
        add_btn.setProperty("compact", True)
        add_btn.setFixedWidth(round(36 * ui_scale()))
        add_btn.setToolTip("Добавить таблицу по ID")
        add_btn.clicked.connect(self._add_sheet)
        sheet_row.addWidget(add_btn)

        del_btn = QPushButton("×")
        del_btn.setProperty("compact", True)
        del_btn.setFixedWidth(round(36 * ui_scale()))
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

        accessibility = QGroupBox("Доступность")
        access_layout = QVBoxLayout(accessibility)
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Масштаб текста:"))
        self.font_scale = AppDoubleSpinBox()
        self.font_scale.setRange(1.0, 1.6)
        self.font_scale.setSingleStep(0.1)
        self.font_scale.setValue(float(self._settings.get("font_scale", 1.0)))
        self.font_scale.setSuffix("×")
        font_row.addWidget(self.font_scale)
        access_layout.addLayout(font_row)
        self.high_contrast = QCheckBox("Высокий контраст")
        self.high_contrast.setChecked(bool(self._settings.get("high_contrast", False)))
        access_layout.addWidget(self.high_contrast)
        layout.addWidget(accessibility)

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
        self._settings["db_path"]                 = self.db_path_edit.text()
        self._settings["google_sheets_id"]        = self._selected_sheet_id()
        self._settings["google_credentials_path"] = self.cred_edit.text()
        self._settings["google_sheets_history"]   = self._history
        self._settings["font_scale"] = self.font_scale.value()
        self._settings["high_contrast"] = self.high_contrast.isChecked()
        # Update last_used for the selected sheet
        selected_id = self._selected_sheet_id()
        if selected_id:
            for entry in self._settings["google_sheets_history"]:
                if entry.get("id") == selected_id:
                    entry["last_used"] = str(date.today())
                    break
        save_settings(self._settings)
        app = QApplication.instance()
        font = app.font()
        font.setPointSizeF(10 * self.font_scale.value())
        app.setFont(font)
        if self.parent() is not None and hasattr(self.parent(), "refresh_theme"):
            self.parent().refresh_theme()
        mb_info(self, "Сохранено", "Настройки сохранены.")
        self.accept()
