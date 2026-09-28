"""Read-only view of recorded score and player changes."""

from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from db.database import get_change_history
from ui.theme import DIALOG_STYLE, style_for


ENTITIES = {
    "day_score": "Очки дня", "total_score": "Итого", "player_name": "Имя",
    "player": "Игрок", "name_lock": "Блокировка", "game_uid": "UID",
    "season": "Сезон",
}
SOURCES = {"manual": "Вручную", "ocr": "OCR", "network": "Захват", "google": "Google Sheets"}


class HistoryDialog(QDialog):
    def __init__(self, season_id: int | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("История изменений")
        self.setStyleSheet(style_for(DIALOG_STYLE))
        self.resize(980, 560)
        layout = QVBoxLayout(self)
        title = QLabel("ИСТОРИЯ ИЗМЕНЕНИЙ")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("Последние 300 изменений. Время показано в UTC."))
        rows = get_change_history(season_id)
        table = QTableWidget(len(rows), 7)
        table.setHorizontalHeaderLabels([
            "Время", "Источник", "Игрок", "Поле", "День", "Было", "Стало"
        ])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for index, row in enumerate(rows):
            values = [
                row["changed_at"], SOURCES.get(row["source"], row["source"]),
                row["player_name"] or "—", ENTITIES.get(row["entity"], row["entity"]),
                str(row["day_number"] or "—"), row["old_value"] or "—",
                row["new_value"] or "—",
            ]
            for col, value in enumerate(values):
                table.setItem(index, col, QTableWidgetItem(str(value)))
        layout.addWidget(table)
