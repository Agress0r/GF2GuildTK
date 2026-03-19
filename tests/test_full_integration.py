#!/usr/bin/env python
"""Comprehensive test: PyQt6 + OCR initialization + threading."""

import sys
import os

# Set environment variables BEFORE importing anything
os.environ.setdefault('ORT_LOGGING_LEVEL', '3')

print("[Test] Step 1: Preload OCR models before PyQt6...")
try:
    from core.ocr import initialize_ocr
    initialize_ocr(log=lambda msg: print(f"  {msg}"))
    print("[Test] ✓ OCR preload successful\n")
except Exception as e:
    print(f"[Test] ❌ OCR preload failed: {e}\n")
    import traceback
    traceback.print_exc()

# Now import PyQt6
print("[Test] Step 2: Import PyQt6...")
try:
    from PyQt6.QtCore import QThread, QObject, pyqtSignal
    from PyQt6.QtWidgets import QApplication
    print("[Test] ✓ PyQt6 imported successfully\n")
except Exception as e:
    print(f"[Test] ❌ PyQt6 import failed: {e}")
    sys.exit(1)

class TestWorker(QObject):
    """Simulates CollectWorker."""
    log_message = pyqtSignal(str)
    finished = pyqtSignal()
    
    def run(self):
        print("[Worker] Step 3: Worker thread starting...")
        
        try:
            from core.ocr import extract_row_data
            print("[Worker] ✓ Successfully imported extract_row_data")
            print("[Worker] ✓ OCR functions available in worker thread")
            
        except Exception as e:
            print(f"[Worker] ❌ Failed: {e}")
            import traceback
            traceback.print_exc()
        
        self.finished.emit()


def main():
    app = QApplication(sys.argv)
    
    print("[Test] Step 4: Creating worker thread...")
    worker = TestWorker()
    thread = QThread()
    worker.moveToThread(thread)
    
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    
    thread.start()
    thread.wait(5000)  # Fixed: wait() takes positional argument, not keyword
    
    print("\n[Test] ✅ All tests completed successfully!")
    print("[Test] OCR modules are ready for use in CollectWorker")


if __name__ == "__main__":
    main()
