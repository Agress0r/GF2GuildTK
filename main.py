import sys
import os
import json
import threading
from app_version import VERSION

if __name__ == "__main__" and sys.argv[1:] == ["--version"]:
    print(VERSION)
    raise SystemExit(0)

# Set environment variables BEFORE importing anything else
os.environ.setdefault('ORT_LOGGING_LEVEL', '3')  # Suppress ONNX Runtime warnings

# Initialize OCR models BEFORE PyQt6 to avoid DLL conflicts
print("Preloading OCR models...", file=sys.stderr)
_ocr_initialization_log = []
try:
    from core.ocr import initialize_ocr
    initialize_ocr(log=_ocr_initialization_log.append)
    print("OCR models loaded successfully", file=sys.stderr)
except Exception as e:
    print(f"Warning: OCR preload failed (will retry if needed): {e}", file=sys.stderr)

# Now safe to import PyQt6
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from pathlib import Path
from ui.main_window import MainWindow
from config.settings_manager import load_settings


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Guild Tracker")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("GuildTracker")
    font = app.font()
    font.setPointSizeF(10 * float(load_settings().get("font_scale", 1.0)))
    app.setFont(font)

    icon_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "GuildBossToolkit.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.setWindowTitle(f"Guild Tracker {VERSION}")
    window.show()

    sys.exit(app.exec())


def capture_diagnostic(output_path: str) -> int:
    """Exercise the same Npcap path used by the UI without opening a window."""
    from core.live_capture import (
        CaptureCancelled, capture_guild_snapshot, diagnose_live_capture,
    )

    result = {"checks": diagnose_live_capture(), "ready": False, "error": ""}
    stop = threading.Event()
    timer = threading.Timer(3, stop.set)
    timer.start()
    try:
        try:
            capture_guild_snapshot(
                stop, on_ready=lambda: result.update(ready=True), timeout=4
            )
        except CaptureCancelled:
            pass
        except Exception as exc:
            result["error"] = str(exc)
    finally:
        timer.cancel()
    from pathlib import Path
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result["ready"] and not result["error"] else 1


def release_check(output_path: str) -> int:
    """Offline packaged smoke test; never opens the user's database/settings."""
    from core.ocr import _get_digit, _get_rapid
    from core.badge_detector import load_templates
    import struct
    import sqlite3
    app = QApplication([sys.argv[0], "-platform", "offscreen"])
    result = {"version": VERSION, "bits": struct.calcsize("P") * 8,
              "digit_ocr": _get_digit() is not None,
              "rapid_ocr": _get_rapid() is not None,
              "ocr_log": _ocr_initialization_log,
              "badge_templates": len(load_templates()), "qt": bool(app)}
    result["ocr"] = result["digit_ocr"] and result["rapid_ocr"]
    conn = sqlite3.connect(":memory:")
    result["sqlite"] = conn.execute("SELECT 1").fetchone()[0] == 1
    conn.close()
    result["ok"] = (result["bits"] == 64 and result["ocr"] and
                    result["badge_templates"] == 4 and result["qt"] and result["sqlite"])
    Path(output_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--release-check":
        raise SystemExit(release_check(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--capture-diagnostic":
        raise SystemExit(capture_diagnostic(sys.argv[2]))
    main()
