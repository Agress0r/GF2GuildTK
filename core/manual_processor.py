"""
Manual image processor.

Takes a list of PIL images, finds badges using badge_detector,
extracts name and total_score for each fully-visible badge via OCR,
deduplicates (max score wins), returns list sorted by score descending.
"""

from __future__ import annotations

from PIL import Image
from typing import Callable

from core.badge_detector import load_templates, find_badges
from core.ocr import _read_text, _read_number


def process_images(
    images: list[Image.Image],
    offsets: dict,
    threshold: float = 0.55,
    log: Callable[[str], None] | None = None,
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

    templates = load_templates()
    if not templates:
        _log("❌ Шаблоны бейджей не загружены. Проверь папку debug_crops/")
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
