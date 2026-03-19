import time
import pyautogui
import pygetwindow as gw
from PIL import Image
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal


class CaptureWorker(QObject):
    """Runs in a QThread — emits signals to update the UI."""

    player_found = pyqtSignal(int, str, int)   # position, name, score
    log_message = pyqtSignal(str)
    finished = pyqtSignal(int)                 # total players collected
    error = pyqtSignal(str)

    def __init__(self, roi: dict, scroll_pause: float = 1.0, scroll_amount: int = 3):
        super().__init__()
        self.roi = roi          # normalised ROI profile from settings
        self.scroll_pause = scroll_pause
        self.scroll_amount = scroll_amount
        self._running = False
        self._screen_w = 0
        self._screen_h = 0

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------ #
    #  Main loop                                                           #
    # ------------------------------------------------------------------ #

    def run(self, season_id: int, db_save_callback):
        """Call this from a QThread."""
        try:
            from core.ocr import extract_row_data
        except ImportError as e:
            self.error.emit(f"OCR module error: {e}")
            return

        self._running = True
        screen_w, screen_h = pyautogui.size()
        self._screen_w = screen_w
        self._screen_h = screen_h

        collected: dict[int, tuple[str, int]] = {}  # position → (name, score)
        last_max_position = -1
        stall_count = 0
        MAX_STALL = 3  # stop after N scrolls with no new players

        self.log_message.emit("Начинаю сбор данных...")

        while self._running:
            screenshot = take_screenshot()
            rows = extract_row_data(screenshot, self.roi, screen_w, screen_h)

            new_found = 0
            for pos, name, score in rows:
                if pos not in collected:
                    collected[pos] = (name, score)
                    new_found += 1
                    self.player_found.emit(pos, name, score)
                    self.log_message.emit(f"  #{pos:>3}  {name}  →  {score:,}")
                    db_save_callback(season_id, name, score, pos)

            current_max = max(collected.keys()) if collected else 0

            if current_max == last_max_position:
                stall_count += 1
                self.log_message.emit(f"Нет новых игроков ({stall_count}/{MAX_STALL})...")
                if stall_count >= MAX_STALL:
                    self.log_message.emit("Таблица закончилась.")
                    break
            else:
                stall_count = 0
                last_max_position = current_max

            scroll_down(self.scroll_amount)
            time.sleep(self.scroll_pause)

        self._running = False
        self.finished.emit(len(collected))


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def take_screenshot() -> Image.Image:
    """Capture the full screen and return a PIL Image."""
    return pyautogui.screenshot()


def scroll_down(clicks: int = 3, ticks_per_row: int = 5):
    """
    Scroll down in the centre of the screen.
    Uses repeated single-tick scrolls with tiny pauses — more reliable
    than one big scroll call, especially in fullscreen games.
    `clicks` = number of rows to advance. `ticks_per_row` controls sensitivity.
    """
    w, h = pyautogui.size()
    cx, cy = w // 2, h // 2
    pyautogui.moveTo(cx, cy)
    total_ticks = clicks * ticks_per_row
    for _ in range(total_ticks):
        pyautogui.scroll(-1)
        time.sleep(0.02)


def crop_roi(image: Image.Image, norm_rect: dict, screen_w: int, screen_h: int) -> Image.Image:
    """
    norm_rect: {"x": 0.0-1.0, "y": 0.0-1.0, "w": 0.0-1.0, "h": 0.0-1.0}
    Returns a cropped PIL Image upscaled 2× for better OCR.
    """
    x = int(norm_rect["x"] * screen_w)
    y = int(norm_rect["y"] * screen_h)
    w = int(norm_rect["w"] * screen_w)
    h = int(norm_rect["h"] * screen_h)
    cropped = image.crop((x, y, x + w, y + h))
    # Upscale for OCR accuracy
    new_w, new_h = cropped.width * 2, cropped.height * 2
    return cropped.resize((new_w, new_h), Image.LANCZOS)
