"""Preview and review a decoded guild score snapshot before SQLite import."""

from __future__ import annotations
from ui.controls import AppSpinBox

from datetime import date
from difflib import get_close_matches

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QHeaderView, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout

from db.database import (
    get_all_player_names_with_lock, get_scores_for_season, get_season,
    import_network_scores,
)
from ui.controls import AppComboBox, ui_scale
from ui.style_utils import mb_critical, mb_info, mb_warning
from ui.theme import DIALOG_STYLE, style_for


class NetworkImportDialog(QDialog):
    def __init__(self, snapshot, season_id: int, season_name: str, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot
        self.season_id = season_id
        self.setWindowTitle("Импорт из сетевого захвата")
        self.setStyleSheet(style_for(DIALOG_STYLE))
        self.resize(940, 650)

        players = get_all_player_names_with_lock()
        by_uid = {p["game_uid"]: p for p in players if p["game_uid"] is not None}
        by_name = {p["name"]: p for p in players}
        scores = {p["name"]: p["total_score"] for p in get_scores_for_season(season_id)}
        self.unmatched: dict[int, AppComboBox] = {}

        layout = QVBoxLayout(self)
        at = snapshot.captured_at.astimezone().strftime("%d.%m.%Y %H:%M:%S")
        layout.addWidget(QLabel(
            f"Клан: {snapshot.guild_name}  |  Игроков: {len(snapshot.players)}  |  "
            f"Захват: {at}  |  Сезон: {season_name}"
        ))
        layout.addWidget(QLabel(
            "Сверьте сезон и день. Для новых имён выберите существующего игрока или создание новой записи."
        ))
        day_row = QHBoxLayout()
        day_row.addWidget(QLabel("День сезона:"))
        self.day_spin = AppSpinBox()
        self.day_spin.setRange(1, 7)
        self.day_spin.setValue(self._suggest_day())
        day_row.addWidget(self.day_spin)
        day_row.addStretch()
        layout.addLayout(day_row)

        self.table = QTableWidget(len(snapshot.players), 5)
        self.table.setHorizontalHeaderLabels([
            "UID", "Имя из игры", "Счёт из игры", "Игрок в базе", "Счёт в базе"
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(round(42 * ui_scale()))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(False)

        for row, player in enumerate(sorted(
            snapshot.players, key=lambda item: (-item.total_score, item.name)
        )):
            existing = by_uid.get(player.uid) or by_name.get(player.name)
            self._set_text(row, 0, str(player.uid))
            self._set_text(row, 1, player.name)
            self._set_text(row, 2, f"{player.total_score:,}")
            if existing:
                self._set_text(row, 3, existing["name"])
                current = scores.get(existing["name"])
                self._set_text(row, 4, f"{current:,}" if current is not None else "—")
            else:
                choice = AppComboBox()
                choice.addItem("Выберите соответствие…", "unresolved")
                choice.addItem("Создать нового игрока", None)
                matches = get_close_matches(player.name, list(by_name), n=3, cutoff=0.35)
                for name in matches:
                    choice.addItem(f"Похожее: {name}", by_name[name]["id"])
                for name in sorted(by_name):
                    if name not in matches:
                        choice.addItem(name, by_name[name]["id"])
                self.table.setCellWidget(row, 3, choice)
                self._set_text(row, 4, "—")
                self.unmatched[player.uid] = choice
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table)

        if self.unmatched:
            layout.addWidget(QLabel(
                f"Не сопоставлено: {len(self.unmatched)}. Проверьте похожие имена перед импортом."
            ))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Импортировать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _suggest_day(self) -> int:
        season = get_season(self.season_id)
        if season and season.get("start_date"):
            try:
                first = date.fromisoformat(season["start_date"])
                day = (self.snapshot.captured_at.astimezone().date() - first).days + 1
                return max(1, min(7, day))
            except ValueError:
                pass
        return 1

    def _set_text(self, row: int, column: int, value: str):
        item = QTableWidgetItem(value)
        if column in (0, 2, 4):
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, column, item)

    def _save(self):
        mapping = {}
        for uid, choice in self.unmatched.items():
            selected = choice.currentData()
            if selected == "unresolved":
                mb_warning(self, "Не сопоставлено", "Выберите действие для каждого нового имени.")
                return
            mapping[uid] = selected
        try:
            result = import_network_scores(
                self.snapshot, self.season_id, self.day_spin.value(), mapping
            )
        except Exception as exc:
            mb_critical(self, "Ошибка импорта", str(exc))
            return
        mb_info(
            self, "Импорт завершён",
            f"Сохранено {result['imported']} игроков; новых: {result['created']}.\n"
            f"Копия базы до импорта: {result['backup_path']}",
        )
        self.accept()
