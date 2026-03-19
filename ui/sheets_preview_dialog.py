"""
Preview dialog for Google Sheets sync.

Shows a comparison table of what's currently in the sheet vs. what will be
written from the local DB, before any API write is made.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QScrollArea, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

from core.sheets_sync import SyncPreview


STYLESHEET = """
QDialog { background: #0a0e17; color: #d0ddf0; font-family: 'Segoe UI', sans-serif; }

QLabel#title { font-size: 17px; font-weight: bold; color: #f0c040; padding: 4px 0; }
QLabel#stats { font-size: 12px; color: #8aabcf; padding: 4px 0; }
QLabel#warn  { font-size: 12px; color: #f08080; padding: 4px 0; }

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
    background: #1a4a7a; border-color: #3a7abf; color: #fff; font-weight: bold;
}
QPushButton#primary:hover { background: #2a5a9a; }

QFrame#divider { background: #162030; }
"""

# Cell colors
BG_CHANGED_UP   = QColor("#2a2a0f")   # value increases  → amber bg
BG_CHANGED_DOWN = QColor("#2a0f0f")   # value decreases  → red bg
FG_CHANGED_UP   = QColor("#f0c040")
FG_CHANGED_DOWN = QColor("#f08080")
FG_UNCHANGED    = QColor("#8aabcf")
BG_STATUS_YES   = QColor("#1a3a1a")
FG_STATUS_YES   = QColor("#80d080")
BG_STATUS_NO    = QColor("#1a2030")
FG_STATUS_NO    = QColor("#8aabcf")


def _fmt(v: int | str) -> str:
    """Format a number with thousands separator."""
    try:
        return f"{int(v):,}".replace(",", " ")
    except (ValueError, TypeError):
        return str(v)


class SheetsPreviewDialog(QDialog):
    def __init__(self, preview: SyncPreview, parent=None):
        super().__init__(parent)
        self._preview = preview
        self.setWindowTitle("Предпросмотр синхронизации — Google Sheets")
        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(900, 560)
        self.resize(1000, 620)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        # Title
        title = QLabel("Предпросмотр синхронизации")
        title.setObjectName("title")
        layout.addWidget(title)

        # Stats row
        p = self._preview
        n_changed  = p.changed_count
        n_same     = p.matched_count - n_changed
        n_unmatched = len(p.unmatched_sheet)
        stats = QLabel(
            f"Совпало: {p.matched_count}  |  "
            f"Изменится: {n_changed}  |  "
            f"Без изменений: {n_same}  |  "
            f"Не найдено в БД: {n_unmatched}"
        )
        stats.setObjectName("stats")
        layout.addWidget(stats)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFixedHeight(1)
        layout.addWidget(div)

        # Main diff table
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
        self._table.setColumnWidth(8, 110)

        self._populate_table()
        layout.addWidget(self._table, stretch=1)

        # Unmatched warnings
        if p.unmatched_sheet:
            lbl = QLabel("Не найдено в БД: " + ", ".join(p.unmatched_sheet))
            lbl.setObjectName("warn")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)
        if p.unmatched_db:
            lbl = QLabel("Нет в таблице (есть в БД): " + ", ".join(p.unmatched_db))
            lbl.setObjectName("warn")
            lbl.setWordWrap(True)
            layout.addWidget(lbl)

        # Buttons
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)

        sync_label = f"Синхронизировать {p.matched_count} игроков"
        self._sync_btn = QPushButton(sync_label)
        self._sync_btn.setObjectName("primary")
        self._sync_btn.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._sync_btn)
        layout.addLayout(btn_row)

    def _populate_table(self):
        diffs = self._preview.diffs
        self._table.setRowCount(len(diffs))

        for row_idx, diff in enumerate(diffs):
            # Name column
            name_item = QTableWidgetItem(diff.sheet_name)
            name_item.setForeground(QColor("#d0ddf0"))
            if diff.db_name and diff.db_name != diff.sheet_name:
                name_item.setToolTip(f"БД: {diff.db_name}")
            self._table.setItem(row_idx, 0, name_item)

            # Day columns
            for day_num in range(1, 8):
                col = day_num  # columns 1–7
                cur_str  = diff.current.get(day_num, "")
                new_val  = diff.proposed.get(day_num, 0)

                try:
                    cur_int = int(cur_str) if cur_str else 0
                except ValueError:
                    cur_int = 0

                changed = str(new_val) != cur_str

                if changed:
                    if new_val > cur_int:
                        text   = f"{_fmt(cur_int)} → {_fmt(new_val)}"
                        fg_col = FG_CHANGED_UP
                        bg_col = BG_CHANGED_UP
                    else:
                        text   = f"{_fmt(cur_int)} → {_fmt(new_val)}"
                        fg_col = FG_CHANGED_DOWN
                        bg_col = BG_CHANGED_DOWN
                    item = QTableWidgetItem(text)
                    item.setForeground(fg_col)
                    item.setBackground(bg_col)
                else:
                    item = QTableWidgetItem(_fmt(new_val) if new_val else "0")
                    item.setForeground(FG_UNCHANGED)

                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row_idx, col, item)

            # Status column
            if diff.has_changes:
                status_item = QTableWidgetItem("✓ Обновится")
                status_item.setForeground(FG_STATUS_YES)
                status_item.setBackground(BG_STATUS_YES)
            else:
                status_item = QTableWidgetItem("= Совпадает")
                status_item.setForeground(FG_STATUS_NO)
                status_item.setBackground(BG_STATUS_NO)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 8, status_item)

        self._table.resizeRowsToContents()
