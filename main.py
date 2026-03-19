import sys
import os

# Set environment variables BEFORE importing anything else
os.environ.setdefault('ORT_LOGGING_LEVEL', '3')  # Suppress ONNX Runtime warnings

# Initialize OCR models BEFORE PyQt6 to avoid DLL conflicts
print("Preloading OCR models...", file=sys.stderr)
try:
    from core.ocr import initialize_ocr
    initialize_ocr()
    print("OCR models loaded successfully", file=sys.stderr)
except Exception as e:
    print(f"Warning: OCR preload failed (will retry if needed): {e}", file=sys.stderr)

# Now safe to import PyQt6
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFontDatabase, QFont, QIcon
from pathlib import Path
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Guild Tracker")
    app.setOrganizationName("GuildTracker")

    icon_path = Path(__file__).parent / "GuildBossToolkit.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
