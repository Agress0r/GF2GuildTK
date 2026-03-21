"""
Preview dialog for reverse Google Sheets sync (Sheets → DB).

Shows a comparison of what's in the sheet vs. what's currently in the DB,
before writing anything to SQLite.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from core.sheets_sync import ReversePreview


STYLESHEET = """
QDialog { background: #0a0e17; color: #d0ddf0; font-family: 'Segoe UI', sans-serif; }

QLabel#title { font-size: 17px; font-weight: bold; color: #f0c040; padding: 4px 0; }
QLabel#stats { font-size: 12px; color: #8aabcf; padding: 4px 0; }
QLabel#warn  { font-size: 12px; color: #f08080; padding: 4px 0; }
QLabel#info  { font-size: 12px; color: #80d080; padding: 4px 0; }

QTableWidget {
    background: #0d1625; color: #d0ddf0;
    border: 1px solid #162030; gridline-color: #162030;
    font-size: 12px;
}
QTableWidget::item { padding: 3px 6px; }
QHeaderView::section {
    background: #0d1a2a; color: #8aabcf;
    border: none; border-right: 1px solid #162030;
    border-bottom: 1px solid #162030;
    padding: 4px 6px; font-size: 12px;
}
QScrollBar:vertical { background: #0a0e17; width: 8px; }
QScrollBar::handle:vertical { background: #2a3a5a; border-radius: 4px; }

QPushButton {
    background: #1e2840; color: #c8d8f0; border: 1px solid #2a3a5a;
    border-radius: 6px; padding: 7px 18px; font-size: 13px;
}
QPushButton:hover { background: #2a3a5a; }
QPushButton#primary {
    background: #1a4a2a; border-color: #3a8a4a; color: #80ff80; font-weight: bold;
}
QPushButton#primary:hover { background: #206030; }

QFrame#divider { background: #162030; }
"""

# Cell colors
BG_NEW_DATA     = QColor("#1a3a1a")   # новые данные (нет в БД)
FG_NEW_DATA     = QColor("#80d080")
BG_CHANGED_UP   = QColor("#2a2a0f")   # значение растёт
FG_CHANGED_UP   = QColor("#f0c040")
BG_CHANGED_DOWN = QColor("#2a0f0f")   # значение падает
FG_CHANGED_DOWN = QColor("#f08080")
FG_UNCHANGED    = QColor("#8aabcf")
FG_NEW_PLAYER   = QColor("#c090f0")
BG_STATUS_IMPORT = QColor("#1a3a1a")
FG_STATUS_IMPORT = QColor("#80d080")
BG_STATUS_NEW    = QColor("#1a1a3a")
FG_STATUS_NEW    = QColor("#c090f0")
BG_STATUS_SAME   = QColor("#1a2030")
FG_STATUS_SAME   = QColor("#8aabcf")


def _fmt(v: int | None) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(v):,}".replace(",", " ")
    except (ValueError, TypeError):
        return str(v)


class SheetsImportPreviewDialog(QDialog):
    def __init__(self, preview: ReversePreview, parent=None):
        super().__init__(parent)
        self._preview = preview
        self.setWindowTitle("Импорт из Google Sheets — предпросмотр")
        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(980, 580)
        self.resize(1060, 640)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        title = QLabel("Предпросмотр импорта из Google Sheets")
        title.setObjectName("title")
        layout.addWidget(title)

        p = self._preview
        n_changed  = p.changed_count
        n_same     = len(p.diffs) - n_changed
        n_new      = p.new_players_count
        stats = QLabel(
            f"Всего в таблице: {len(p.diffs)}  |  "
            f"Будет записано: {n_changed}  |  "
            f"Без изменений: {n_same}  |  "
            f"Новых игроков: {n_new}"
        )
        stats.setObjectName("stats")
        layout.addWidget(stats)

        div = QFrame()
        div.setObjectName("divider")
        div.setFixedHeight(1)
        layout.addWidget(div)

        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "Имя", "Дн.1", "Дн.2", "Дн.3", "Дн.4", "Дн.5", "Дн.6", "Дн.7", "Статус"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 8):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(col, 110)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(8, 120)

        self._populate_table()
        layout.addWidget(self._table, stretch=1)

        if n_new > 0:
            lbl = QLabel(f"Фиолетовым выделены новые игроки ({n_new}), которых нет в БД — будут созданы автоматически.")
            lbl.setObjectName("info")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)

        import_label = f"Импортировать {n_changed} игроков"
        self._import_btn = QPushButton(import_label)
        self._import_btn.setObjectName("primary")
        self._import_btn.setEnabled(n_changed > 0)
        self._import_btn.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._import_btn)
        layout.addLayout(btn_row)

    def _populate_table(self):
        diffs = self._preview.diffs
        self._table.setRowCount(len(diffs))

        for row_idx, diff in enumerate(diffs):
            # Имя
            name_item = QTableWidgetItem(diff.sheet_name)
            if diff.is_new_player:
                name_item.setForeground(FG_NEW_PLAYER)
                name_item.setToolTip("Новый игрок — будет создан в БД")
            elif diff.db_name and diff.db_name != diff.sheet_name:
                name_item.setForeground(QColor("#d0ddf0"))
                name_item.setToolTip(f"БД: {diff.db_name}")
            else:
                name_item.setForeground(QColor("#d0ddf0"))
            self._table.setItem(row_idx, 0, name_item)

            # Дни 1-7
            for day_num in range(1, 8):
                col = day_num
                delta    = diff.sheet_deltas.get(day_num, 0)
                new_snap = diff.sheet_snapshots.get(day_num, 0)
                db_snap  = diff.db_snapshots.get(day_num)

                if db_snap is None:
                    # Нет данных в БД
                    if delta == 0:
                        item = QTableWidgetItem("0")
                        item.setForeground(FG_UNCHANGED)
                    else:
                        item = QTableWidgetItem(f"+{_fmt(delta)}")
                        item.setForeground(FG_NEW_DATA)
                        item.setBackground(BG_NEW_DATA)
                        item.setToolTip(f"Новые данные: снапшот будет {_fmt(new_snap)}")
                else:
                    # Данные есть — сравниваем снапшоты
                    if new_snap == db_snap:
                        item = QTableWidgetItem(_fmt(delta) if delta else "0")
                        item.setForeground(FG_UNCHANGED)
                    elif new_snap > db_snap:
                        item = QTableWidgetItem(f"{_fmt(delta)}")
                        item.setForeground(FG_CHANGED_UP)
                        item.setBackground(BG_CHANGED_UP)
                        item.setToolTip(f"Снапшот: {_fmt(db_snap)} → {_fmt(new_snap)}")
                    else:
                        item = QTableWidgetItem(f"{_fmt(delta)}")
                        item.setForeground(FG_CHANGED_DOWN)
                        item.setBackground(BG_CHANGED_DOWN)
                        item.setToolTip(f"Снапшот: {_fmt(db_snap)} → {_fmt(new_snap)}")

                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row_idx, col, item)

            # Статус
            if diff.is_new_player and diff.has_changes:
                status_item = QTableWidgetItem("+ Новый игрок")
                status_item.setForeground(FG_STATUS_NEW)
                status_item.setBackground(BG_STATUS_NEW)
            elif diff.has_changes:
                status_item = QTableWidgetItem("✓ Импортируется")
                status_item.setForeground(FG_STATUS_IMPORT)
                status_item.setBackground(BG_STATUS_IMPORT)
            else:
                status_item = QTableWidgetItem("= Совпадает")
                status_item.setForeground(FG_STATUS_SAME)
                status_item.setBackground(BG_STATUS_SAME)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 8, status_item)

        self._table.resizeRowsToContents()
