"""
Manual image processor.

Takes a list of PIL images, finds badges using badge_detector,
extracts name and total_score for each fully-visible badge via OCR,
deduplicates (max score wins), returns list sorted by score descending.
"""

from __future__ import annotations

import numpy as np
from pathlib import Path
from PIL import Image
from typing import Callable

from core.badge_detector import load_templates, find_badges
from core.ocr import _read_text, _read_number

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


def process_images(
    images: list[Image.Image],
    offsets: dict,
    threshold: float = 0.55,
    log: Callable[[str], None] | None = None,
    mode: str = "gs",
) -> list[tuple[str, int]]:
    """
    Process a list of screenshots.

    Args:
        images:    List of PIL images.
        offsets:   Badge offset calibration dict with keys 'name' and 'score',
                   each containing {dx, dy, w, h}.
        threshold: Badge match confidence (0–1).
        log:       Optional log callback.

    Returns:
        List of (player_name, total_score) sorted by score descending.
        Duplicates resolved by keeping the maximum score.
    """
    def _log(msg: str):
        if log:
            log(msg)

    templates = load_templates(mode=mode)
    if not templates:
        from core.badge_detector import MODE_CONFIG
        tdir = MODE_CONFIG.get(mode, {}).get("templates_dir", "assets/badges")
        _log(f"❌ Шаблоны бейджей не загружены. Проверь папку {tdir}/")
        return []

    _log(f"Загружено шаблонов: {len(templates)}")

    name_off  = offsets.get("name", {})
    score_off = offsets.get("score", {})

    if not name_off or not score_off:
        _log("❌ Калибровка смещений не настроена.")
        return []

    # name → best score seen so far
    collected: dict[str, int] = {}

    for img_idx, image in enumerate(images):
        _log(f"\n── Изображение {img_idx + 1}/{len(images)} ({image.width}×{image.height}) ──")

        badges = find_badges(image, templates, threshold=threshold, offsets=offsets)
        _log(f"  Найдено полностью видимых бейджей: {len(badges)}")

        for badge_idx, (bx, by, bw, bh) in enumerate(badges):
            try:
                name_crop  = _crop(image, bx + name_off["dx"],  by + name_off["dy"],
                                   name_off["w"],  name_off["h"])
                score_crop = _crop(image, bx + score_off["dx"], by + score_off["dy"],
                                   score_off["w"], score_off["h"])

                if mode == "fc":
                    score_crop = _erase_symbol(score_crop)
                    score_crop = _binarize_score(score_crop)

                name  = _read_text(name_crop,  label=f"img{img_idx+1}_b{badge_idx+1}_name")
                score = _read_number(score_crop, label=f"img{img_idx+1}_b{badge_idx+1}_score")

                if not name:
                    _log(f"    [badge {badge_idx+1}] ⚠ Пустое имя — пропуск")
                    continue
                if score is None:
                    _log(f"    [badge {badge_idx+1}] ⚠ Score не распознан — пропуск")
                    continue

                name = name.strip()
                if name not in collected or score > collected[name]:
                    if name in collected:
                        _log(f"    [badge {badge_idx+1}] Дубль {name!r}: {collected[name]} → {score} (обновление)")
                    collected[name] = score
                    _log(f"    [badge {badge_idx+1}] ✓  {name}  →  {score:,}")
                else:
                    _log(f"    [badge {badge_idx+1}] Дубль {name!r}: score {score} ≤ {collected[name]}, оставляем старый")

            except Exception as e:
                _log(f"    [badge {badge_idx+1}] ❌ {e}")

    results = sorted(collected.items(), key=lambda kv: kv[1], reverse=True)
    _log(f"\n✅ Итого уникальных игроков: {len(results)}")
    return results  # [(name, score), ...]


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _crop(image: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image:
    """Crop and 2× upscale for better OCR accuracy."""
    cropped = image.crop((x, y, x + w, y + h))
    return cropped.resize((cropped.width * 2, cropped.height * 2), Image.LANCZOS)


# Cached symbol template (loaded once, scaled to 2× to match _crop upscale)
_symbol_template: np.ndarray | None = None


def _load_symbol_template() -> np.ndarray | None:
    """Load and cache the FC score symbol template at 2× scale."""
    global _symbol_template
    if _symbol_template is not None:
        return _symbol_template
    if not _CV2_AVAILABLE:
        return None
    path = Path("assets/SymbolWhiteBlack.png")
    if not path.exists():
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    # Scale 2× to match _crop upscale
    h, w = img.shape[:2]
    img = cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_LINEAR)
    _symbol_template = img
    return _symbol_template


def _binarize_score(score_crop: Image.Image) -> Image.Image:
    """Convert score crop to clean black digits on white background via Otsu threshold."""
    if not _CV2_AVAILABLE:
        return score_crop
    gray = cv2.cvtColor(np.array(score_crop.convert("RGB")), cv2.COLOR_RGB2GRAY)
    # Otsu binarization → clean black/white
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Ensure dark-on-light: if the majority of pixels are dark, invert
    if np.mean(binary) < 128:
        binary = 255 - binary
    # Slight morphological close to fix broken strokes
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    return Image.fromarray(binary)


def _erase_symbol(score_crop: Image.Image) -> Image.Image:
    """Find the FC score symbol in the crop and fill it with background color."""
    tmpl = _load_symbol_template()
    if tmpl is None:
        return score_crop

    gray = cv2.cvtColor(np.array(score_crop.convert("RGB")), cv2.COLOR_RGB2GRAY)
    th, tw = tmpl.shape[:2]
    if th > gray.shape[0] or tw > gray.shape[1]:
        return score_crop

    result = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= 0.5:
        x, y = max_loc
        # Fill matched region with median background color (edges of the crop)
        bg = int(np.median(gray[:, :5]))  # left edge as background sample
        arr = np.array(score_crop.convert("RGB"))
        arr[y:y + th, x:x + tw] = bg
        return Image.fromarray(arr)

    return score_crop
