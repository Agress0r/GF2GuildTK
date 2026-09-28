"""
Preview dialog for Google Sheets sync.

Shows a comparison table of what's currently in the sheet vs. what will be
written from the local DB, before any API write is made.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QLineEdit, QCheckBox,
    QHeaderView, QAbstractItemView, QFrame, QApplication,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from core.sheets_sync import SyncPreview
from ui.controls import ComparisonChoice, ui_scale
from ui.theme import DIALOG_STYLE, style_for, theme_color


STYLESHEET = DIALOG_STYLE + """
QTableWidget { font-size: 12px; }
QTableWidget::item { padding: 5px 7px; }
QHeaderView::section { padding: 8px 7px; font-size: 11px; }
QScrollBar:vertical { background: #0b0c0e; width: 8px; }
QScrollBar::handle:vertical { background: #353840; border-radius: 2px; }
"""

# Cell colors
FG_UNCHANGED    = QColor("#a4a094")
BG_STATUS_YES   = QColor("#1f3027")
FG_STATUS_YES   = QColor("#9ad7aa")
BG_STATUS_NO    = QColor("#1d1f24")
FG_STATUS_NO    = QColor("#a4a094")


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
        self.setStyleSheet(style_for(STYLESHEET))
        self.setMinimumSize(980, 560)
        screen = QApplication.primaryScreen()
        self.resize(max(980, min(1680, screen.availableGeometry().width() - 60)), 720)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        # Title
        eyebrow = QLabel("GOOGLE SHEETS  /  ЭКСПОРТ")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("Изменения перед отправкой")
        title.setObjectName("title")
        layout.addWidget(title)

        # Stats row
        p = self._preview
        source = QLabel(f"Лист: {p.worksheet_title or 'выбранный лист'}")
        source.setObjectName("info")
        layout.addWidget(source)
        n_changed  = p.changed_count
        n_same     = p.matched_count - n_changed
        n_unmatched = len(p.unmatched_sheet)
        self._stats = QLabel(
            f"Совпало: {p.matched_count}  |  "
            f"Изменится: {n_changed}  |  "
            f"Без изменений: {n_same}  |  "
            f"Не найдено в БД: {n_unmatched}"
        )
        self._stats.setObjectName("stats")
        stats_card = QFrame()
        stats_card.setObjectName("summary_card")
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.addWidget(self._stats)
        layout.addWidget(stats_card)
        info = QLabel("Для каждой спорной ячейки можно отправить значение БД или сохранить значение таблицы.")
        info.setObjectName("info")
        layout.addWidget(info)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        div.setFixedHeight(1)
        layout.addWidget(div)

        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Найти игрока…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_rows)
        self._changes_only = QCheckBox("Только изменения")
        self._changes_only.stateChanged.connect(self._filter_rows)
        filters.addWidget(self._search, stretch=1)
        filters.addWidget(self._changes_only)
        layout.addLayout(filters)

        # Main diff table
        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "Имя", "Дн.1", "Дн.2", "Дн.3", "Дн.4", "Дн.5", "Дн.6", "Дн.7", "Статус"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, 8):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self._table.setColumnWidth(col, round(205 * ui_scale()))
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(8, 145)

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

        sync_label = f"Экспортировать {p.changed_count} игроков"
        self._sync_btn = QPushButton(sync_label)
        self._sync_btn.setObjectName("primary")
        self._sync_btn.setEnabled(p.changed_count > 0)
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
            name_item.setForeground(theme_color(QColor("#ece8dc")))
            if diff.db_name and diff.db_name != diff.sheet_name:
                name_item.setToolTip(f"БД: {diff.db_name}")
            self._table.setItem(row_idx, 0, name_item)

            # Day columns
            for day_num in range(1, 8):
                col = day_num  # columns 1–7
                cur_str  = diff.current.get(day_num, "")
                new_val  = diff.proposed.get(day_num, 0)

                changed = str(new_val) != cur_str

                if changed:
                    choice = ComparisonChoice(
                        ("db", "БД", _fmt(new_val)),
                        ("sheet", "Таблица", _fmt(cur_str) if cur_str else "—"),
                        diff.choices.get(day_num, "db"),
                    )
                    choice.setToolTip(f"День {day_num}: выберите значение для Google Таблицы")
                    choice.changed.connect(
                        lambda source, d=diff, day=day_num: self._select_cell(d, day, source)
                    )
                    self._table.setCellWidget(row_idx, col, choice)
                    continue

                item = QTableWidgetItem(_fmt(new_val) if new_val else "0")
                item.setForeground(theme_color(FG_UNCHANGED))

                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row_idx, col, item)

            # Status column
            if diff.selected_changes():
                status_item = QTableWidgetItem("✓ Обновится")
                status_item.setForeground(theme_color(FG_STATUS_YES))
                status_item.setBackground(theme_color(BG_STATUS_YES))
            else:
                status_item = QTableWidgetItem("= Совпадает")
                status_item.setForeground(theme_color(FG_STATUS_NO))
                status_item.setBackground(theme_color(BG_STATUS_NO))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 8, status_item)
            self._table.setRowHeight(
                row_idx,
                round((78 if any(self._table.cellWidget(row_idx, col)
                                 for col in range(1, 8)) else 40) * ui_scale()),
            )

    def _select_cell(self, diff, day: int, source: str):
        diff.choices[day] = source
        row = next((index for index, item in enumerate(self._preview.diffs) if item is diff), None)
        if row is not None:
            status = self._table.item(row, 8)
            if status:
                status.setText("✓ Обновится" if diff.selected_changes() else "= Оставить таблицу")
                status.setForeground(FG_STATUS_YES if diff.selected_changes() else FG_STATUS_NO)
                status.setBackground(BG_STATUS_YES if diff.selected_changes() else BG_STATUS_NO)
        changed = self._preview.changed_count
        self._stats.setText(
            f"Совпало: {self._preview.matched_count}  |  Изменится: {changed}  |  "
            f"Без изменений: {self._preview.matched_count - changed}  |  "
            f"Не найдено в БД: {len(self._preview.unmatched_sheet)}"
        )
        self._sync_btn.setText(f"Экспортировать {changed} игроков")
        self._sync_btn.setEnabled(changed > 0)
        self._filter_rows()

    def _filter_rows(self, *_):
        query = self._search.text().strip().casefold()
        changed_only = self._changes_only.isChecked()
        for row, diff in enumerate(self._preview.diffs):
            hidden = (query and query not in diff.sheet_name.casefold()) or (
                changed_only and not diff.selected_changes()
            )
            self._table.setRowHidden(row, bool(hidden))
