"""Check OCR/Qt initialization in a clean process, not during test collection."""

import os
from pathlib import Path
import subprocess
import sys


def test_ocr_models_and_qt_in_fresh_process():
    code = """
from concurrent.futures import ThreadPoolExecutor
from core import ocr
ocr.initialize_ocr()
assert ocr._get_digit() is not None
assert ocr._get_rapid() is not None
from PyQt6.QtWidgets import QApplication
from PIL import Image, ImageDraw, ImageFont
app = QApplication([])
image = Image.new('RGB', (300, 60), 'white')
ImageDraw.Draw(image).text((10, 10), 'Alice', font=ImageFont.truetype('arial.ttf', 32), fill='black')
with ThreadPoolExecutor(max_workers=1) as executor:
    result = executor.submit(ocr._read_text, image).result(timeout=30)
assert result == 'Alice', result
from core.badge_detector import load_templates
from core.manual_processor import process_images
import cv2
badge = Image.fromarray(cv2.cvtColor(load_templates()[0], cv2.COLOR_BGR2RGB))
screen = Image.new('RGB', (1000, max(300, badge.height + 40)), 'white')
screen.paste(badge, (10, 20))
x = badge.width + 50
draw = ImageDraw.Draw(screen)
font = ImageFont.truetype('arial.ttf', 24)
draw.text((x, 20), 'Alice', font=font, fill='black')
draw.text((x + 300, 20), '1000', font=font, fill='black')
offsets = {'name': {'dx': x - 20, 'dy': -5, 'w': 210, 'h': 50},
           'score': {'dx': x + 280, 'dy': -5, 'w': 150, 'h': 50}}
rows = process_images([screen], offsets, threshold=0.9)
assert [(row.name, row.score) for row in rows] == [('Alice', 1000)], rows
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
