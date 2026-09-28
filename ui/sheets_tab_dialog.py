"""Choose a visible Google Sheets worksheet before importing data."""

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ui.controls import AppComboBox
from ui.theme import DIALOG_STYLE, style_for


class SheetsTabDialog(QDialog):
    def __init__(self, worksheets: list[tuple[int, str]], preferred_id: int | None,
                 spreadsheet_name: str, parent=None, *, exporting=False):
        super().__init__(parent)
        self.setWindowTitle("Лист для экспорта" if exporting else "Лист для импорта")
        self.setStyleSheet(style_for(DIALOG_STYLE))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        title = QLabel("Экспорт в Google Таблицу" if exporting else "Импорт из Google Таблицы")
        title.setObjectName("title")
        layout.addWidget(title)
        document = QLabel(f"Таблица: {spreadsheet_name}")
        document.setWordWrap(True)
        layout.addWidget(document)
        layout.addWidget(QLabel("Выбранный лист:"))
        self.combo = AppComboBox()
        for worksheet_id, worksheet_title in worksheets:
            self.combo.addItem(worksheet_title, worksheet_id)
        index = self.combo.findData(preferred_id)
        self.combo.setCurrentIndex(index if index >= 0 else self.combo.count() - 1)
        layout.addWidget(self.combo)
        info = QLabel("Приложение запомнит выбранный лист для этой таблицы.")
        info.setObjectName("info")
        info.setWordWrap(True)
        layout.addWidget(info)
        warning = QLabel("После проверки изменений выбранные значения будут записаны "
                         + ("в Google Таблицу." if exporting else "в локальную БД."))
        warning.setWordWrap(True)
        layout.addWidget(warning)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        proceed = QPushButton("Загрузить лист")
        proceed.setEnabled(bool(worksheets))
        proceed.setObjectName("primary")
        proceed.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(proceed)
        layout.addLayout(buttons)

    @property
    def worksheet_id(self) -> int:
        return int(self.combo.currentData())
