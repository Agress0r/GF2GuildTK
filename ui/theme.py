"""Shared Qt palette and accessibility preferences."""

import re

from config.settings_manager import load_settings

DIALOG_STYLE = """
QDialog, QMessageBox, QInputDialog { background: #0b0c0e; color: #ccc8bc; font-family: 'Segoe UI', sans-serif; }
QLabel { color: #ccc8bc; font-size: 12px; }
QLabel#title { color: #e8b84b; font-size: 17px; font-weight: 700; padding: 4px 0; }
QLabel#eyebrow { color: #8c8a7e; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QLabel#stats { color: #a4a094; font-size: 12px; padding: 4px 0; }
QLabel#warn { color: #ef9d8f; font-size: 12px; }
QLabel#info { color: #e8b84b; font-size: 12px; }
QTableWidget { background: #0b0c0e; alternate-background-color: #13141a; color: #ccc8bc; border: 1px solid #22242c; gridline-color: #22242c; selection-background-color: #332919; }
QHeaderView::section { background: #13141a; color: #a4a094; border: none; border-bottom: 1px solid #353840; padding: 8px; font-weight: 700; }
QTableWidget::item { padding: 5px; border-bottom: 1px solid #22242c; }
QFrame#summary_card { background: #13141a; border: 1px solid #353840; border-radius: 3px; padding: 8px; }
QCheckBox { color: #a4a094; padding: 4px; }
QCheckBox::indicator { width: 14px; height: 14px; }
QLineEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit { background: #17181d; color: #ece8dc; border: 1px solid #353840; border-radius: 3px; padding: 6px; }
QComboBox { padding: 5px 30px 5px 8px; }
QComboBox:hover, QComboBox:focus { border-color: #c8922a; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 26px; border-left: 1px solid #353840; background: #242329; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView { background: #17181d; color: #ece8dc; selection-background-color: #332919; outline: 0; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 5px 8px; }
QPushButton { background: #17181d; color: #ccc8bc; border: 1px solid #353840; border-radius: 3px; padding: 8px 14px; }
QPushButton:hover { background: #242329; border-color: #8a6118; }
QPushButton:default, QPushButton#primary { background: #2a2115; color: #e8b84b; border-color: #8a6118; font-weight: 700; }
QPushButton:disabled { color: #66645e; border-color: #22242c; }
QFrame#divider { background: #353840; }
QPushButton { font-size: 12px; }
QPushButton:pressed { background: #332919; }
QPushButton#primary:hover, QPushButton#process_btn:hover, QPushButton#save_btn:hover { background: #3a2b16; }
QPushButton#process_btn, QPushButton#save_btn { background: #2a2115; color: #e8b84b; border-color: #8a6118; font-weight: 700; }
QPushButton#danger, QPushButton#remove_btn { background: #381d1d; color: #ef9d8f; border-color: #8a3a3a; }
QPushButton#danger:hover, QPushButton#remove_btn:hover { background: #4a2a2a; }
QPushButton#primary:disabled, QPushButton#process_btn:disabled, QPushButton#save_btn:disabled { color: #66645e; background: #17181d; border-color: #353840; }
QListWidget { background: #13141a; color: #ccc8bc; border: 1px solid #353840; }
QListWidget::item:selected { background: #332919; }
QProgressBar { background: #1d1f24; border: 1px solid #353840; text-align: center; }
QProgressBar::chunk { background: #c8922a; }
QPushButton[compact="true"] { padding: 2px 6px; }
QSpinBox, QDoubleSpinBox { padding-right: 24px; }

QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right;
    width:18px; border-left:1px solid #353840; border-bottom:1px solid #353840;
    border-top-right-radius:4px; background:#1d1f24; }

QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover { background:#353840; }

QDoubleSpinBox::up-button:pressed, QSpinBox::up-button:pressed { background:#332919; }

QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right;
    width:18px; border-left:1px solid #353840; border-top:1px solid #353840;
    border-bottom-right-radius:4px; background:#1d1f24; }

QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover { background:#353840; }

QDoubleSpinBox::down-button:pressed, QSpinBox::down-button:pressed { background:#332919; }

AppDoubleSpinBox::up-arrow, AppSpinBox::up-arrow {
    image: none; width:0; height:0; }

AppDoubleSpinBox::down-arrow, AppSpinBox::down-arrow {
    image: none; width:0; height:0; }
"""


def style_for(css: str) -> str:
    """Apply the selected contrast and text scale to a window stylesheet."""
    settings = load_settings()
    scale = max(1.0, min(1.6, float(settings.get("font_scale", 1.0))))
    if scale != 1:
        css = re.sub(
            r"font-size:\s*(\d+)(px|pt)",
            lambda m: f"font-size: {round(int(m.group(1)) * scale)}{m.group(2)}",
            css,
        )
    if settings.get("high_contrast", False):
        for old, new in {
            "#0b0c0e": "#000000", "#0f1014": "#080808",
            "#101115": "#080808", "#13141a": "#101010",
            "#17181d": "#151515", "#1d1f24": "#202020",
            "#22242c": "#505050", "#353840": "#777777",
            "#ccc8bc": "#ffffff", "#a4a094": "#eeeeee",
            "#8c8a7e": "#dddddd", "#777267": "#c0c0c0",
            "#e8b84b": "#ffdd55", "#c8922a": "#ffcc33",
            "#8a6118": "#ffcc33", "#332919": "#403000",
        }.items():
            css = css.replace(old, new)
    return css


def theme_color(value):
    """Apply the same contrast palette to explicitly painted table cells."""
    from PyQt6.QtGui import QColor
    color = QColor(value)
    return QColor(style_for(color.name()))
