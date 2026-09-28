"""Match the application's Windows DLL initialization order before Qt imports."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")

from core.ocr import initialize_ocr

initialize_ocr()
