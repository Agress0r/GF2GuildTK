"""
Standalone snipping process.

Called by the main app via QProcess:
    python ui/snip_proc.py <output_png_path>

Exit code 0 = success (PNG saved to output path).
Exit code 1 = cancelled or error.
"""

import sys
import os
import traceback

# Ensure project root is on the path so 'ui.screen_snip' can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    import pyautogui
    from PyQt6.QtWidgets import QApplication
    from ui.screen_snip import ScreenSnipOverlay
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: snip_proc.py <output_path>", file=sys.stderr)
        sys.exit(1)

    out_path = sys.argv[1]
    
    try:
        # Check if QApplication already exists
        app = QApplication.instance()
        if app is None:
            # Create QApplication with empty sys.argv to avoid conflicts
            app = QApplication([])
        
        # Take screenshot
        pil = pyautogui.screenshot()
        
        # Show snipping overlay
        snip = ScreenSnipOverlay(pil)
        result = snip.exec()
        
        crop = snip.crop

        if crop is not None:
            crop.save(out_path, "PNG")
            print(f"Saved crop to {out_path}", file=sys.stderr)
            sys.exit(0)
        else:
            print("User cancelled snipping", file=sys.stderr)
            sys.exit(1)
        
    except Exception as e:
        print(f"Error in snip_proc: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
