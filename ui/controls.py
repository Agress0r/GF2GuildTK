"""Shared, readable selectors used throughout the application."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPolygon
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox, QLabel, QSizePolicy, QVBoxLayout, QWidget
from config.settings_manager import load_settings
from ui.theme import style_for


def ui_scale() -> float:
    return max(1.0, min(1.6, float(load_settings().get("font_scale", 1.0))))


class _SpinArrows:
    """Qt stylesheets do not reliably render CSS border triangles on Windows."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(style_for("#a4a094")))
        x = self.width() - 10
        for y, direction in [(self.height() // 4, -1), (3 * self.height() // 4, 1)]:
            painter.drawPolygon(QPolygon([QPoint(x - 4, y - direction * 2),
                                           QPoint(x + 4, y - direction * 2),
                                           QPoint(x, y + direction * 3)]))


class AppSpinBox(_SpinArrows, QSpinBox):
    pass


class AppDoubleSpinBox(_SpinArrows, QDoubleSpinBox):
    pass


class AppComboBox(QComboBox):
    """A dropdown with a usable click target and a popup wide enough for its text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(round(34 * ui_scale()))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._arrow_color = QColor(
            "#ffdd55" if load_settings().get("high_contrast") else "#e8b84b"
        )

    def showPopup(self):
        metrics = self.view().fontMetrics()
        text_width = max((metrics.horizontalAdvance(self.itemText(i))
                          for i in range(self.count())), default=0)
        self.view().setMinimumWidth(max(self.width(), min(text_width + 44, 420)))
        super().showPopup()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._arrow_color)
        x = self.width() - 14
        y = self.height() // 2
        painter.drawPolygon(QPolygon([QPoint(x - 5, y - 2), QPoint(x + 5, y - 2), QPoint(x, y + 4)]))


class ComparisonChoice(QWidget):
    """Show both values and the selected result for a sync conflict."""

    changed = pyqtSignal(str)

    def __init__(
        self,
        first: tuple[str, str, str],
        second: tuple[str, str, str],
        selected: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("comparison_choice")
        self._values = {source: value for source, _, value in (first, second)}
        self._labels: dict[str, QLabel] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(1)
        for source, title, value in (first, second):
            label = QLabel(f"{title}: {value}")
            label.setToolTip(f"{title}: {value}")
            self._labels[source] = label
            layout.addWidget(label)
        self.combo = AppComboBox()
        self.combo.setObjectName("comparison_combo")
        for source, title, value in (first, second):
            short_title = "Табл" if source == "sheet" else title
            self.combo.addItem(f"{short_title} {value}", source)
        self.combo.setCurrentIndex(max(0, self.combo.findData(selected)))
        self.combo.currentIndexChanged.connect(self._on_change)
        layout.addWidget(self.combo)
        self._refresh()

    def _on_change(self, *_):
        self._refresh()
        self.changed.emit(self.currentData())

    def _refresh(self):
        chosen = self.currentData()
        for source, label in self._labels.items():
            label.setStyleSheet(style_for(
                "color: #e8b84b; font-weight: 700; font-size: 11px;" if source == chosen
                else "color: #a4a094; font-size: 11px;"
            ))
        self.combo.setToolTip(f"Итоговое значение: {self._values.get(chosen, '—')}")

    def currentData(self) -> str:
        return self.combo.currentData()

    def setCurrentIndex(self, index: int) -> None:
        self.combo.setCurrentIndex(index)
