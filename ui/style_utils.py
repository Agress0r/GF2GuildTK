"""Dark-themed wrappers for Qt standard dialogs.

Use these instead of bare QMessageBox.warning/critical/information/question
and QInputDialog.getText/getInt so that dialogs match the app's dark theme.
"""

from PyQt6.QtWidgets import QMessageBox, QInputDialog, QWidget
from ui.theme import DIALOG_STYLE, style_for

_DARK = DIALOG_STYLE



def mb_warning(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setStyleSheet(style_for(_DARK))
    box.exec()


def mb_critical(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Critical, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setStyleSheet(style_for(_DARK))
    box.exec()


def mb_info(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Information, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setStyleSheet(style_for(_DARK))
    box.exec()


def mb_question(parent: QWidget, title: str, text: str) -> bool:
    """Returns True if the user clicked Yes."""
    box = QMessageBox(
        QMessageBox.Icon.Question, title, text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    box.setStyleSheet(style_for(_DARK))
    return box.exec() == QMessageBox.StandardButton.Yes


def ask_text(parent: QWidget, title: str, label: str,
             text: str = "") -> tuple[str, bool]:
    """Dark-themed replacement for QInputDialog.getText."""
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    dlg.setStyleSheet(style_for(_DARK))
    ok = dlg.exec() == QInputDialog.DialogCode.Accepted
    return dlg.textValue(), ok


def ask_int(parent: QWidget, title: str, label: str,
            value: int = 1, min_val: int = 0, max_val: int = 99) -> tuple[int, bool]:
    """Dark-themed replacement for QInputDialog.getInt."""
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setInputMode(QInputDialog.InputMode.IntInput)
    dlg.setIntValue(value)
    dlg.setIntMinimum(min_val)
    dlg.setIntMaximum(max_val)
    dlg.setStyleSheet(style_for(_DARK))
    ok = dlg.exec() == QInputDialog.DialogCode.Accepted
    return dlg.intValue(), ok
