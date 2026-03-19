#!/usr/bin/env python
"""Test OCR initialization in main thread and worker thread."""

import sys
sys.path.insert(0, r'F:\AI\GF2TableToolkit')

from PyQt6.QtCore import QThread, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
import time

class TestWorker(QObject):
    """Simulates the CollectWorker behavior."""
    log_message = pyqtSignal(str)
    finished = pyqtSignal()
    
    def run(self):
        self.log_message.emit("[Worker] Starting in thread...")
        
        try:
            from core.ocr import extract_row_data
            self.log_message.emit("[Worker] ✓ OCR module imported successfully")
        except Exception as e:
            self.log_message.emit(f"[Worker] ❌ Failed to import OCR: {e}")
            self.finished.emit()
            return
        
        self.log_message.emit("[Worker] Test completed successfully!")
        self.finished.emit()


def main():
    app = QApplication(sys.argv)
    
    # Pre-initialize OCR in main thread
    print("[Main] Pre-initializing OCR in main thread...")
    from core.ocr import initialize_ocr
    initialize_ocr(log=print)
    print("[Main] OCR pre-initialization complete\n")
    
    # Create and run worker in separate thread
    print("[Main] Starting worker in separate thread...")
    worker = TestWorker()
    thread = QThread()
    worker.moveToThread(thread)
    
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    worker.log_message.connect(print)
    
    thread.start()
    
    # Wait for thread to finish
    thread.wait()
    print("\n[Main] All tests completed!")


if __name__ == "__main__":
    main()
