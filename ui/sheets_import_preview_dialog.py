"""
Preview dialog for reverse Google Sheets sync (Sheets → DB).

Shows a comparison of what's in the sheet vs. what's currently in the DB,
before writing anything to SQLite.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QLineEdit, QCheckBox,
    QHeaderView, QAbstractItemView, QFrame, QApplication,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from core.sheets_sync import ReversePreview
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
BG_NEW_DATA     = QColor("#1f3027")   # новые данные (нет в БД)
FG_NEW_DATA     = QColor("#9ad7aa")
BG_CHANGED_UP   = QColor("#332919")   # значение растёт
FG_CHANGED_UP   = QColor("#e8b84b")
BG_CHANGED_DOWN = QColor("#381d1d")   # значение падает
FG_CHANGED_DOWN = QColor("#ef9d8f")
FG_UNCHANGED    = QColor("#a4a094")
FG_NEW_PLAYER   = QColor("#d5b5e8")
BG_STATUS_IMPORT = QColor("#1f3027")
FG_STATUS_IMPORT = QColor("#9ad7aa")
BG_STATUS_NEW    = QColor("#27202e")
FG_STATUS_NEW    = QColor("#d5b5e8")
BG_STATUS_SAME   = QColor("#1d1f24")
FG_STATUS_SAME   = QColor("#a4a094")


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
        self.setStyleSheet(style_for(STYLESHEET))
        self.setMinimumSize(980, 580)
        screen = QApplication.primaryScreen()
        self.resize(max(980, min(1680, screen.availableGeometry().width() - 60)), 720)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        eyebrow = QLabel("GOOGLE SHEETS  /  ИМПОРТ")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("Изменения перед записью в БД")
        title.setObjectName("title")
        layout.addWidget(title)

        p = self._preview
        if p.worksheet_title:
            source = QLabel(f"Источник: лист «{p.worksheet_title}»")
            source.setObjectName("info")
            layout.addWidget(source)
        if p.active_day is not None:
            active_text = (f"Активные дни: 1–{p.active_day}. Нули будущих дней не импортируются."
                           if p.active_day else "На листе нет дней с ненулевыми очками.")
            active_label = QLabel(active_text)
            active_label.setObjectName("info")
            layout.addWidget(active_label)
        n_changed  = p.changed_count
        n_same     = len(p.diffs) - n_changed
        n_new      = p.new_players_count
        self._stats = QLabel(
            f"Всего в таблице: {len(p.diffs)}  |  "
            f"Будет записано: {n_changed}  |  "
            f"Без изменений: {n_same}  |  "
            f"Будет создано игроков: {n_new}"
        )
        self._stats.setObjectName("stats")
        stats_card = QFrame()
        stats_card.setObjectName("summary_card")
        stats_layout = QVBoxLayout(stats_card)
        stats_layout.addWidget(self._stats)
        layout.addWidget(stats_card)
        info = QLabel("В изменённых ячейках выберите значение из таблицы или сохраните локальное значение БД.")
        info.setObjectName("info")
        layout.addWidget(info)

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

        if n_new > 0:
            lbl = QLabel("Игроки, которых нет в БД, будут созданы при выборе значений таблицы.")
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
        self._update_selection_stats()
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._import_btn)
        layout.addLayout(btn_row)
        self._filter_rows()

    def _filter_rows(self, *_):
        query = self._search.text().strip().casefold()
        changed_only = self._changes_only.isChecked()
        for row, diff in enumerate(self._preview.diffs):
            hidden = (query and query not in diff.sheet_name.casefold()) or (
                changed_only and not diff.selected_changes()
            )
            self._table.setRowHidden(row, bool(hidden))

    def _populate_table(self):
        diffs = self._preview.diffs
        self._table.setRowCount(len(diffs))

        for row_idx, diff in enumerate(diffs):
            # Имя
            name_item = QTableWidgetItem(diff.sheet_name)
            if diff.is_new_player:
                name_item.setForeground(theme_color(FG_NEW_PLAYER))
                name_item.setToolTip("Новый игрок — будет создан в БД")
            elif diff.db_name and diff.db_name != diff.sheet_name:
                name_item.setForeground(theme_color(QColor("#ece8dc")))
                name_item.setToolTip(f"БД: {diff.db_name}")
            else:
                name_item.setForeground(theme_color(QColor("#ece8dc")))
            self._table.setItem(row_idx, 0, name_item)

            # Дни 1-7
            for day_num in range(1, 8):
                col = day_num
                delta    = diff.sheet_deltas.get(day_num, 0)
                new_snap = diff.sheet_snapshots.get(day_num, 0)
                db_snap  = diff.db_snapshots.get(day_num)

                if diff.sheet_present and not diff.sheet_present.get(day_num, False):
                    item = QTableWidgetItem("—")
                    item.setForeground(theme_color(FG_UNCHANGED))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setToolTip("На листе нет данных для импорта за этот день; значение БД сохранится.")
                    self._table.setItem(row_idx, col, item)
                    continue

                if new_snap != db_snap and (
                    not diff.is_new_player or diff.sheet_present.get(day_num)
                ):
                    choice = ComparisonChoice(
                        ("sheet", "Таблица", _fmt(new_snap)),
                        ("db", "БД", _fmt(db_snap)),
                        diff.choices.get(day_num, "sheet"),
                    )
                    choice.setToolTip(f"День {day_num}: выберите значение для локальной базы")
                    choice.changed.connect(
                        lambda source, d=diff, day=day_num: self._select_cell(d, day, source)
                    )
                    self._table.setCellWidget(row_idx, col, choice)
                    continue

                if db_snap is None:
                    # Нет данных в БД
                    if delta == 0:
                        item = QTableWidgetItem("0")
                        item.setForeground(theme_color(FG_UNCHANGED))
                    else:
                        item = QTableWidgetItem(f"+{_fmt(delta)}")
                        item.setForeground(theme_color(FG_NEW_DATA))
                        item.setBackground(theme_color(BG_NEW_DATA))
                        item.setToolTip(f"Новые данные: снапшот будет {_fmt(new_snap)}")
                else:
                    # Данные есть — сравниваем снапшоты
                    if new_snap == db_snap:
                        item = QTableWidgetItem(_fmt(delta) if delta else "0")
                        item.setForeground(theme_color(FG_UNCHANGED))
                    elif new_snap > db_snap:
                        item = QTableWidgetItem(f"{_fmt(delta)}")
                        item.setForeground(theme_color(FG_CHANGED_UP))
                        item.setBackground(theme_color(BG_CHANGED_UP))
                        item.setToolTip(f"Снапшот: {_fmt(db_snap)} → {_fmt(new_snap)}")
                    else:
                        item = QTableWidgetItem(f"{_fmt(delta)}")
                        item.setForeground(theme_color(FG_CHANGED_DOWN))
                        item.setBackground(theme_color(BG_CHANGED_DOWN))
                        item.setToolTip(f"Снапшот: {_fmt(db_snap)} → {_fmt(new_snap)}")

                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row_idx, col, item)

            # Статус
            if diff.is_new_player and diff.selected_changes():
                status_item = QTableWidgetItem("+ Новый игрок")
                status_item.setForeground(theme_color(FG_STATUS_NEW))
                status_item.setBackground(theme_color(BG_STATUS_NEW))
            elif diff.selected_changes():
                status_item = QTableWidgetItem("✓ Импортируется")
                status_item.setForeground(theme_color(FG_STATUS_IMPORT))
                status_item.setBackground(theme_color(BG_STATUS_IMPORT))
            else:
                status_item = QTableWidgetItem("= Совпадает")
                status_item.setForeground(theme_color(FG_STATUS_SAME))
                status_item.setBackground(theme_color(BG_STATUS_SAME))
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
            if status is not None:
                if diff.selected_changes():
                    status.setText("+ Новый игрок" if diff.is_new_player else "✓ Импортируется")
                    status.setForeground(FG_STATUS_NEW if diff.is_new_player else FG_STATUS_IMPORT)
                    status.setBackground(BG_STATUS_NEW if diff.is_new_player else BG_STATUS_IMPORT)
                else:
                    status.setText("= Локальные данные")
                    status.setForeground(theme_color(FG_STATUS_SAME))
                    status.setBackground(theme_color(BG_STATUS_SAME))
        self._update_selection_stats()
        self._filter_rows()

    def _update_selection_stats(self):
        p = self._preview
        changed = p.changed_count
        self._stats.setText(
            f"Всего в таблице: {len(p.diffs)}  |  Будет записано: {changed}  |  "
            f"Без изменений: {len(p.diffs) - changed}  |  Будет создано игроков: {p.new_players_count}"
        )
        self._import_btn.setText(f"Импортировать {changed} игроков")
        self._import_btn.setEnabled(changed > 0)
