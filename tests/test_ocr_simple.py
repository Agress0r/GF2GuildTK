#!/usr/bin/env python
"""Simple OCR module test without PyQt6."""

import sys
sys.path.insert(0, r'F:\AI\GF2TableToolkit')

print("[Test] Testing OCR module import...")

try:
    from core import ocr
    print(f"[Test] ✓ core.ocr module imported")
except Exception as e:
    print(f"[Test] ❌ Failed to import core.ocr: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("[Test] Testing initialize_ocr()...")
try:
    ocr.initialize_ocr(log=print)
    print(f"[Test] ✓ initialize_ocr() completed")
except Exception as e:
    print(f"[Test] ❌ Failed in initialize_ocr(): {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("[Test] All tests passed!")
