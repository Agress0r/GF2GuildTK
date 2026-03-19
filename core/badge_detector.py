"""
Badge detector — finds badge positions in a screenshot using cv2.matchTemplate.

Templates are loaded from assets/badges/ and scaled down by TEMPLATE_SCALE
(they were saved at 3× zoom by the calibration tool).

Usage:
    templates = load_templates()
    badges = find_badges(pil_image, templates, threshold=0.75)
    # badges: list of (x, y, w, h) sorted top-to-bottom
"""

from __future__ import annotations

import numpy as np
from pathlib import Path
from PIL import Image

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

# Badge templates are saved at 3× original resolution
TEMPLATE_SCALE = 1 / 3.0

TEMPLATES_DIR = Path("assets/badges")
TEMPLATE_NAMES = ["B1Gold", "B2Silver", "B3Bronze", "B4Default"]


# ------------------------------------------------------------------ #
#  Template loading                                                    #
# ------------------------------------------------------------------ #

def load_templates() -> list[np.ndarray]:
    """
    Load badge templates from debug_crops/, scale them down to original size.
    Returns list of BGR numpy arrays ready for cv2.matchTemplate.
    """
    if not _CV2_AVAILABLE:
        return []

    templates = []
    for name in TEMPLATE_NAMES:
        path = TEMPLATES_DIR / f"{name}.png"
        if not path.exists():
            continue
        img = cv2.imread(str(path))
        if img is None:
            continue
        h, w = img.shape[:2]
        new_w = max(1, int(w * TEMPLATE_SCALE))
        new_h = max(1, int(h * TEMPLATE_SCALE))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        templates.append(resized)

    return templates


# ------------------------------------------------------------------ #
#  Badge finding                                                       #
# ------------------------------------------------------------------ #

def find_badges(
    image: Image.Image,
    templates: list[np.ndarray],
    threshold: float = 0.75,
    offsets: dict | None = None,
) -> list[tuple[int, int, int, int]]:
    """
    Find all badge occurrences in *image*.

    Args:
        image:     PIL Image (full screenshot or cropped region).
        templates: Output of load_templates().
        threshold: Match confidence threshold (0–1).
        offsets:   If provided, also filters out badges where the name/score
                   zones extend outside the image bounds.

    Returns:
        List of (x, y, w, h) tuples sorted top-to-bottom. NMS applied.
    """
    if not _CV2_AVAILABLE or not templates:
        return []

    img_bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    img_h, img_w = img_bgr.shape[:2]

    all_boxes: list[tuple[int, int, int, int]] = []
    all_scores: list[float] = []

    for tmpl in templates:
        th, tw = tmpl.shape[:2]
        if th > img_h or tw > img_w:
            continue  # template larger than image — skip

        result = cv2.matchTemplate(img_bgr, tmpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        for x, y in zip(xs, ys):
            all_boxes.append((int(x), int(y), int(tw), int(th)))
            all_scores.append(float(result[y, x]))

    boxes = _nms(all_boxes, all_scores)

    if offsets:
        boxes = _filter_fully_visible(boxes, offsets, img_w, img_h)

    # Sort top-to-bottom
    return sorted(boxes, key=lambda b: b[1])


# ------------------------------------------------------------------ #
#  NMS                                                                 #
# ------------------------------------------------------------------ #

def _nms(
    boxes: list[tuple[int, int, int, int]],
    scores: list[float],
    iou_threshold: float = 0.3,
) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []

    arr = np.array([(x, y, x + w, y + h) for x, y, w, h in boxes], dtype=float)
    sc = np.array(scores)
    order = sc.argsort()[::-1]
    keep: list[int] = []

    while order.size > 0:
        i = order[0]
        keep.append(i)

        xx1 = np.maximum(arr[i, 0], arr[order[1:], 0])
        yy1 = np.maximum(arr[i, 1], arr[order[1:], 1])
        xx2 = np.minimum(arr[i, 2], arr[order[1:], 2])
        yy2 = np.minimum(arr[i, 3], arr[order[1:], 3])

        inter_w = np.maximum(0.0, xx2 - xx1)
        inter_h = np.maximum(0.0, yy2 - yy1)
        inter = inter_w * inter_h

        area_i = (arr[i, 2] - arr[i, 0]) * (arr[i, 3] - arr[i, 1])
        area_j = (arr[order[1:], 2] - arr[order[1:], 0]) * (arr[order[1:], 3] - arr[order[1:], 1])
        iou = inter / (area_i + area_j - inter + 1e-6)

        order = order[1:][iou < iou_threshold]

    return [boxes[i] for i in keep]


# ------------------------------------------------------------------ #
#  Visibility filter                                                   #
# ------------------------------------------------------------------ #

def _filter_fully_visible(
    boxes: list[tuple[int, int, int, int]],
    offsets: dict,
    img_w: int,
    img_h: int,
) -> list[tuple[int, int, int, int]]:
    """Keep only badges where badge + name zone + score zone all fit inside the image."""
    result = []
    for bx, by, bw, bh in boxes:
        if not _zone_visible(bx, by, bw, bh, img_w, img_h):
            continue
        name_off = offsets.get("name", {})
        score_off = offsets.get("score", {})
        if name_off and not _zone_visible(
            bx + name_off["dx"], by + name_off["dy"],
            name_off["w"], name_off["h"], img_w, img_h,
        ):
            continue
        if score_off and not _zone_visible(
            bx + score_off["dx"], by + score_off["dy"],
            score_off["w"], score_off["h"], img_w, img_h,
        ):
            continue
        result.append((bx, by, bw, bh))
    return result


def _zone_visible(x: int, y: int, w: int, h: int, img_w: int, img_h: int) -> bool:
    return x >= 0 and y >= 0 and x + w <= img_w and y + h <= img_h
