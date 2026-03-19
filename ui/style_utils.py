"""Dark-themed wrappers for Qt standard dialogs.

Use these instead of bare QMessageBox.warning/critical/information/question
and QInputDialog.getText/getInt so that dialogs match the app's dark theme.
"""

from PyQt6.QtWidgets import QMessageBox, QInputDialog, QWidget

_DARK = """
QDialog, QMessageBox {
    background: #0a0e17;
    color: #d0ddf0;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}
QLabel {
    color: #d0ddf0;
    font-size: 13px;
}
QTextEdit, QTextBrowser {
    background: #0a0e17;
    color: #d0ddf0;
    border: none;
}
QLineEdit {
    background: #1a2035;
    color: #d0ddf0;
    border: 1px solid #2a3a5a;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 13px;
}
QSpinBox {
    background: #1a2035;
    color: #d0ddf0;
    border: 1px solid #2a3a5a;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
}
QSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border-left: 1px solid #2a3a5a;
    border-bottom: 1px solid #2a3a5a; border-top-right-radius: 4px;
    background: #1e2840;
}
QSpinBox::up-button:hover { background: #2a3a5a; }
QSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border-left: 1px solid #2a3a5a;
    border-top: 1px solid #2a3a5a; border-bottom-right-radius: 4px;
    background: #1e2840;
}
QSpinBox::down-button:hover { background: #2a3a5a; }
QSpinBox::up-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid #8aabcf;
}
QSpinBox::down-arrow {
    image: none; width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #8aabcf;
}
QPushButton {
    background: #1e2840;
    color: #c8d8f0;
    border: 1px solid #2a3a5a;
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 13px;
    min-width: 80px;
}
QPushButton:hover  { background: #2a3a5a; }
QPushButton:pressed { background: #384870; }
QPushButton:default {
    background: #1a4a7a;
    border-color: #3a7abf;
    color: #ffffff;
    font-weight: bold;
}
QPushButton:default:hover { background: #2a5a9a; }
"""


def mb_warning(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setStyleSheet(_DARK)
    box.exec()


def mb_critical(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Critical, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setStyleSheet(_DARK)
    box.exec()


def mb_info(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Icon.Information, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    box.setStyleSheet(_DARK)
    box.exec()


def mb_question(parent: QWidget, title: str, text: str) -> bool:
    """Returns True if the user clicked Yes."""
    box = QMessageBox(
        QMessageBox.Icon.Question, title, text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    box.setStyleSheet(_DARK)
    return box.exec() == QMessageBox.StandardButton.Yes


def ask_text(parent: QWidget, title: str, label: str,
             text: str = "") -> tuple[str, bool]:
    """Dark-themed replacement for QInputDialog.getText."""
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    dlg.setStyleSheet(_DARK)
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
    dlg.setStyleSheet(_DARK)
    ok = dlg.exec() == QInputDialog.DialogCode.Accepted
    return dlg.intValue(), ok
