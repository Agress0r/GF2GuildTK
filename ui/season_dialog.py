from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDateEdit
)
from ui.style_utils import mb_info, mb_question
from PyQt6.QtCore import Qt, pyqtSignal, QDate
from PyQt6.QtGui import QColor
from db.database import get_all_seasons, create_season, delete_season

STYLESHEET = """
QDialog { background:#0a0e17; color:#d0ddf0; font-family:'Segoe UI',sans-serif; }
QLabel { color:#c8d8f0; font-size:13px; }
QLabel#title { font-size:18px; font-weight:bold; color:#f0c040; padding:8px 0; }
QLineEdit, QSpinBox, QDateEdit {
    background:#1a2035; color:#d0ddf0; border:1px solid #2a3a5a;
    border-radius:4px; padding:5px 8px; font-size:13px; }
QDateEdit::drop-down { border:none; width:20px; }
QDateEdit::down-arrow { image:none; }
QPushButton {
    background:#1e2840; color:#c8d8f0; border:1px solid #2a3a5a;
    border-radius:6px; padding:7px 16px; font-size:13px; }
QPushButton:hover { background:#2a3a5a; }
QPushButton#primary { background:#1a4a7a; border-color:#3a7abf; color:#fff; font-weight:bold; }
QPushButton#primary:hover { background:#2a5a9a; }
QPushButton#danger { background:#4a1a1a; border-color:#8a3a3a; color:#ff9090; }
QPushButton#danger:hover { background:#6a2a2a; }
QTableWidget {
    background:#111820; color:#c8d8f0; gridline-color:#1e2a3a;
    border:1px solid #2a3a5a; border-radius:4px; font-size:13px; }
QHeaderView::section {
    background:#1a2435; color:#8aabcf; border:none;
    padding:6px; font-size:12px; font-weight:bold; }
QTableWidget::item:selected { background:#1e3a5a; color:#ffffff; }
"""


class SeasonDialog(QDialog):
    season_selected = pyqtSignal(int, str)  # id, display name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сезоны — Guild Tracker")
        self.setStyleSheet(STYLESHEET)
        self.setMinimumSize(720, 480)
        self._build_ui()
        self._load_seasons()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Управление сезонами")
        title.setObjectName("title")
        layout.addWidget(title)

        # Create new season form
        form_row = QHBoxLayout()
        form_row.addWidget(QLabel("Сезон №"))
        self.num_spin = QSpinBox()
        self.num_spin.setRange(1, 9999)
        self.num_spin.setValue(1)
        form_row.addWidget(self.num_spin)

        form_row.addWidget(QLabel("Название:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("необязательно")
        form_row.addWidget(self.name_edit, stretch=1)

        form_row.addWidget(QLabel("Начало:"))
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate())
        self.start_date_edit.setDisplayFormat("dd.MM.yyyy")
        self.start_date_edit.setFixedWidth(120)
        form_row.addWidget(self.start_date_edit)

        form_row.addWidget(QLabel("Конец:"))
        self.end_date_label = QLabel("")
        self.end_date_label.setFixedWidth(90)
        form_row.addWidget(self.end_date_label)

        create_btn = QPushButton("+ Создать")
        create_btn.setObjectName("primary")
        create_btn.clicked.connect(self._create_season)
        form_row.addWidget(create_btn)
        layout.addLayout(form_row)

        # Connect date change to auto-update end label
        self.start_date_edit.dateChanged.connect(self._on_start_date_changed)
        self._on_start_date_changed(self.start_date_edit.date())

        # Table: 5 columns now
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["№", "Название", "Период", "Создан", ""])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table, stretch=1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        self.select_btn = QPushButton("✔  Выбрать сезон")
        self.select_btn.setObjectName("primary")
        self.select_btn.clicked.connect(self._select_season)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        btn_row.addWidget(self.select_btn)
        layout.addLayout(btn_row)

    def _on_start_date_changed(self, qdate: QDate):
        end = qdate.addDays(6)
        self.end_date_label.setText(end.toString("dd.MM.yyyy"))

    def _load_seasons(self):
        seasons = get_all_seasons()
        self.table.setRowCount(len(seasons))
        self._season_ids = []
        for i, s in enumerate(seasons):
            self._season_ids.append(s["id"])
            self.table.setItem(i, 0, QTableWidgetItem(str(s["number"])))
            self.table.setItem(i, 1, QTableWidgetItem(s.get("name") or ""))

            # Period column
            start = s.get("start_date") or ""
            end = s.get("end_date") or ""
            if start and end:
                period_text = f"{start} — {end}"
            elif start:
                period_text = f"с {start}"
            else:
                period_text = "—"
            self.table.setItem(i, 2, QTableWidgetItem(period_text))

            self.table.setItem(i, 3, QTableWidgetItem(s.get("created_at", "")[:10]))
            del_btn = QPushButton("Удалить")
            del_btn.setObjectName("danger")
            del_btn.clicked.connect(lambda _, sid=s["id"]: self._delete_season(sid))
            self.table.setCellWidget(i, 4, del_btn)
        self.table.resizeColumnsToContents()
        if seasons:
            self.num_spin.setValue(seasons[0]["number"] + 1)

    def _create_season(self):
        num  = self.num_spin.value()
        name = self.name_edit.text().strip()
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.start_date_edit.date().addDays(6).toString("yyyy-MM-dd")
        create_season(num, name, start_date=start_date, end_date=end_date)
        self._load_seasons()

    def _delete_season(self, season_id: int):
        if mb_question(self, "Удалить сезон?",
                       "Все данные этого сезона будут удалены. Продолжить?"):
            delete_season(season_id)
            self._load_seasons()

    def _select_season(self):
        row = self.table.currentRow()
        if row < 0:
            mb_info(self, "Выбор", "Выберите сезон в таблице.")
            return
        sid  = self._season_ids[row]
        name = f"Сезон {self.table.item(row,0).text()}"
        if self.table.item(row, 1).text():
            name += f" — {self.table.item(row,1).text()}"
        self.season_selected.emit(sid, name)
        self.accept()
