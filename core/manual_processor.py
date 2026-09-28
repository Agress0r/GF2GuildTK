"""
Manual image processor.

Takes a list of PIL images, finds badges using badge_detector,
extracts name and total_score for each fully-visible badge via OCR,
deduplicates (max score wins), returns list sorted by score descending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from PIL import Image
from typing import Callable

from core.badge_detector import load_templates, find_badges
from core.ocr import _read_text_with_confidence, _read_number


@dataclass
class OcrResult:
    name: str
    score: int
    confidence: float
    occurrences: int = 1
    score_variants: set[int] = field(default_factory=set)


def process_images(
    images: list[Image.Image],
    offsets: dict | Callable[[Image.Image], dict | None],
    threshold: float = 0.55,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[OcrResult]:
    """
    Process a list of screenshots.

    Args:
        images:    List of PIL images.
        offsets:   Calibration dict or resolver for each image resolution.
        threshold: Badge match confidence (0–1).
        log:       Optional log callback.

    Returns:
        OCR candidates sorted by score. Duplicate evidence is retained.
    """
    def _log(msg: str):
        if log:
            log(msg)

    templates = load_templates()
    if not templates:
        _log("❌ Шаблоны бейджей не загружены из assets/badges.")
        return []

    _log(f"Загружено шаблонов: {len(templates)}")

    collected: dict[str, OcrResult] = {}

    for img_idx, image in enumerate(images):
        if should_cancel and should_cancel():
            _log("Обработка отменена пользователем.")
            break
        _log(f"\n── Изображение {img_idx + 1}/{len(images)} ({image.width}×{image.height}) ──")

        image_offsets = offsets(image) if callable(offsets) else offsets
        name_off = (image_offsets or {}).get("name", {})
        score_off = (image_offsets or {}).get("score", {})
        if not name_off or not score_off:
            _log("⚠ Нет профиля калибровки для этого изображения — пропуск.")
            if progress:
                progress(img_idx + 1, len(images))
            continue

        badges = find_badges(image, templates, threshold=threshold, offsets=image_offsets)
        _log(f"  Найдено полностью видимых бейджей: {len(badges)}")

        for badge_idx, (bx, by, bw, bh) in enumerate(badges):
            if should_cancel and should_cancel():
                break
            try:
                name_crop  = _crop(image, bx + name_off["dx"],  by + name_off["dy"],
                                   name_off["w"],  name_off["h"])
                score_crop = _crop(image, bx + score_off["dx"], by + score_off["dy"],
                                   score_off["w"], score_off["h"])

                name, confidence = _read_text_with_confidence(
                    name_crop, label=f"img{img_idx+1}_b{badge_idx+1}_name"
                )
                score = _read_number(score_crop, label=f"img{img_idx+1}_b{badge_idx+1}_score")

                if not name:
                    _log(f"    [badge {badge_idx+1}] ⚠ Пустое имя — пропуск")
                    continue
                if score is None:
                    _log(f"    [badge {badge_idx+1}] ⚠ Score не распознан — пропуск")
                    continue

                name = name.strip()
                if name in collected:
                    candidate = collected[name]
                    candidate.occurrences += 1
                    candidate.score_variants.add(score)
                    if score > candidate.score:
                        candidate.score = score
                        candidate.confidence = confidence
                    _log(f"    [badge {badge_idx+1}] Дубль {name!r}: {candidate.occurrences} совпадений")
                else:
                    collected[name] = OcrResult(name, score, confidence, score_variants={score})
                    _log(f"    [badge {badge_idx+1}] ✓  {name}  →  {score:,} ({confidence:.0%})")

            except Exception as e:
                _log(f"    [badge {badge_idx+1}] ❌ {e}")
        if progress:
            progress(img_idx + 1, len(images))

    results = sorted(collected.values(), key=lambda result: result.score, reverse=True)
    _log(f"\n✅ Итого уникальных игроков: {len(results)}")
    return results


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _crop(image: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image:
    """Crop and 2× upscale for better OCR accuracy."""
    cropped = image.crop((x, y, x + w, y + h))
    return cropped.resize((cropped.width * 2, cropped.height * 2), Image.LANCZOS)
