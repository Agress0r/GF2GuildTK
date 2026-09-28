"""Single version source for the application, build metadata and release tools."""

from pathlib import Path
import re
import sys

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
if not re.fullmatch(r"\d+\.\d+\.\d+", VERSION):
    raise ValueError("VERSION must contain MAJOR.MINOR.PATCH")
VERSION_TUPLE = tuple(int(part) for part in VERSION.split(".")) + (0,)
if any(part > 65535 for part in VERSION_TUPLE):
    raise ValueError("Windows version components cannot exceed 65535")
