"""
OCR module — RapidOCR for names (Chinese/English, ONNX-based, no PyTorch)
             ddddocr   for digit fields (badge numbers, scores)

Install: pip install rapidocr-onnxruntime ddddocr
No PyTorch, no Tesseract, no GPU required.
"""

from __future__ import annotations
import re
import io
import traceback
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance
import numpy as np

_rapid_ocr = None   # RapidOCR  — names / any text
_digit_ocr = None   # ddddocr   — numbers only
_init_attempted = False  # Flag to prevent repeated init attempts on failure


def initialize_ocr(log=None):
    """Pre-initialize OCR models in the main thread.
    Call this BEFORE starting any worker threads to avoid DLL load issues in Windows.
    """
    global _init_attempted
    if _init_attempted:
        return
    _init_attempted = True
    
    try:
        _get_digit(log)
    except Exception:
        pass  # Logged inside _get_digit
    
    try:
        _get_rapid(log)
    except Exception:
        pass  # Logged inside _get_rapid


def _get_rapid(log=None):
    global _rapid_ocr
    if _rapid_ocr is None:
        _log(log, "  [OCR] Инициализация RapidOCR…")
        try:
            from rapidocr_onnxruntime import RapidOCR
            _rapid_ocr = RapidOCR()
            _log(log, "  [OCR] RapidOCR готов ✓")
        except Exception as e:
            _log(log, f"  [OCR] ❌ RapidOCR недоступен: {e}")
            # Don't raise here - return None to let caller handle gracefully
            _rapid_ocr = False  # Mark as failed
            return None
    return _rapid_ocr if _rapid_ocr is not False else None


def _get_digit(log=None):
    global _digit_ocr
    if _digit_ocr is None:
        _log(log, "  [OCR] Инициализация ddddocr…")
        try:
            import ddddocr
            _digit_ocr = ddddocr.DdddOcr(show_ad=False)
            _log(log, "  [OCR] ddddocr готов ✓")
        except Exception as e:
            _log(log, f"  [OCR] ❌ ddddocr недоступен: {e}")
            # Don't raise here - return None to let fallback work
            _digit_ocr = False  # Mark as failed
            return None
    return _digit_ocr if _digit_ocr is not False else None


def _log(log, msg: str):
    if log:
        log(msg)


# ------------------------------------------------------------------ #
#  Public API                                                          #
# ------------------------------------------------------------------ #

def extract_row_data(
    screenshot: Image.Image,
    roi: dict,
    screen_w: int,
    screen_h: int,
    log=None,
    save_debug: bool = False,
    screenshot_index: int = 0,
) -> list[tuple[int, str, int]]:
    """
    Returns list of (position, name, total_score).
    log        — callable(str) piped to UI journal
    save_debug — dump every crop to debug_crops/ folder
    """
    from core.capture import crop_roi

    rows_cfg = roi.get("rows", [])
    _log(log, f"[Скриншот #{screenshot_index}] ROI: {len(rows_cfg)} строк | экран {screen_w}x{screen_h}")

    if not rows_cfg:
        _log(log, "  ⚠ ROI пустой. Пересохрани калибровку.")
        return []

    results = []
    for i, row in enumerate(rows_cfg):
        _log(log, f"  ── Строка {i + 1}/{len(rows_cfg)} ──")
        try:
            badge_rect = row.get("badge")
            name_rect  = row.get("name")
            score_rect = row.get("score")

            if not all([badge_rect, name_rect, score_rect]):
                _log(log, "    ⚠ Пропуск: отсутствует зона badge/name/score в ROI")
                continue

            # Log pixel coordinates for sanity check
            for zone, rect in [("badge", badge_rect), ("name", name_rect), ("score", score_rect)]:
                px = int(rect["x"] * screen_w)
                py = int(rect["y"] * screen_h)
                pw = int(rect["w"] * screen_w)
                ph = int(rect["h"] * screen_h)
                _log(log, f"    {zone:5s}: px=({px},{py}) size={pw}x{ph}")

            badge_img = crop_roi(screenshot, badge_rect, screen_w, screen_h)
            name_img  = crop_roi(screenshot, name_rect,  screen_w, screen_h)
            score_img = crop_roi(screenshot, score_rect, screen_w, screen_h)

            _log(log, f"    Кропы (после upscale): badge={badge_img.size} "
                      f"name={name_img.size} score={score_img.size}")

            if save_debug:
                _save_debug_crop(badge_img, f"s{screenshot_index:03d}_r{i + 1}_badge")
                _save_debug_crop(name_img,  f"s{screenshot_index:03d}_r{i + 1}_name")
                _save_debug_crop(score_img, f"s{screenshot_index:03d}_r{i + 1}_score")

            position = _read_number(badge_img, label=f"badge r{i + 1}", log=log)
            name     = _read_text(name_img,    label=f"name  r{i + 1}", log=log)
            score    = _read_number(score_img, label=f"score r{i + 1}", log=log)

            _log(log, f"    → pos={position!r}  name={name!r}  score={score!r}")

            if position is None:
                _log(log, "    ⚠ Пропуск: позиция не распознана")
                continue
            if not name:
                _log(log, "    ⚠ Пропуск: имя пустое")
                continue
            if score is None:
                _log(log, "    ⚠ Пропуск: счёт не распознан")
                continue

            results.append((position, name.strip(), score))
            _log(log, f"    ✓ #{position}  {name}  →  {score:,}")

        except Exception as e:
            _log(log, f"    ❌ Исключение в строке {i + 1}: {e}")
            _log(log, f"       {traceback.format_exc().splitlines()[-1]}")

    _log(log, f"[Скриншот #{screenshot_index}] Принято {len(results)}/{len(rows_cfg)}")
    return results


# ------------------------------------------------------------------ #
#  Number reader — ddddocr (ONNX, no PyTorch)                        #
# ------------------------------------------------------------------ #

def _read_number(img: Image.Image, label: str = "", log=None) -> int | None:
    # Convert PIL to bytes for ddddocr
    try:
        ocr = _get_digit(log)
        if ocr is None:
            _log(log, f"    [dddd {label}] недоступна → пробую RapidOCR")
        else:
            buf = io.BytesIO()
            _preprocess(img).save(buf, format="PNG")
            raw = ocr.classification(buf.getvalue())
            digits = re.sub(r"\D", "", str(raw))
            _log(log, f"    [dddd {label}] raw={raw!r}  digits={digits!r}")
            if digits:
                return int(digits)
            _log(log, f"    [dddd {label}] пусто → пробую RapidOCR")
    except Exception as e:
        _log(log, f"    [dddd {label}] ❌ {e} → пробую RapidOCR")

    # Fallback: RapidOCR (filter to digits afterwards)
    try:
        ocr   = _get_rapid(log)
        if ocr is None:
            _log(log, f"    [rapid {label}] тоже недоступна")
            return None
        result, _ = ocr(_preprocess_np(img))
        texts = [r[1] for r in result] if result else []
        joined = re.sub(r"\D", "", "".join(texts))
        _log(log, f"    [rapid {label}] raw={texts!r}  digits={joined!r}")
        return int(joined) if joined else None
    except Exception as e:
        _log(log, f"    [rapid {label}] ❌ {e}")
        return None


# ------------------------------------------------------------------ #
#  Text reader — RapidOCR (ONNX, Chinese + Latin)                    #
# ------------------------------------------------------------------ #

def _read_text(img: Image.Image, label: str = "", log=None) -> str:
    try:
        ocr = _get_rapid(log)
        if ocr is None:
            _log(log, f"    [rapid {label}] недоступна")
            return ""
        # Add padding so characters near crop edges are not cut off
        from PIL import ImageOps
        padded = ImageOps.expand(img, border=8, fill=(255, 255, 255))
        # use_det=False: skip region detector, treat entire crop as one text line.
        # This eliminates fragmentation where the detector splits one name into
        # multiple boxes and the recognizer produces garbled fragments.
        result, _ = ocr(np.array(padded), use_det=False, use_cls=False)
        # With use_det=False output is [[text, score], ...] (no bbox),
        # so text is r[0]; with det=True it would be r[1]. Handle both.
        if result:
            texts = [r[0] if len(r) == 2 else r[1] for r in result]
        else:
            texts = []
        # Player names never contain spaces; remove any spaces the recognizer adds
        text = "".join(texts).replace(" ", "").strip()
        _log(log, f"    [rapid {label}] raw={texts!r}  →  {text!r}")
        return text
    except Exception as e:
        _log(log, f"    [rapid {label}] ❌ {e}")
        _log(log, f"       {traceback.format_exc().splitlines()[-1]}")
        return ""


# ------------------------------------------------------------------ #
#  Image preprocessing                                                #
# ------------------------------------------------------------------ #

def _preprocess(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.5)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def _preprocess_np(img: Image.Image) -> np.ndarray:
    return np.array(_preprocess(img))


# ------------------------------------------------------------------ #
#  Debug helpers                                                      #
# ------------------------------------------------------------------ #

def _save_debug_crop(img: Image.Image, name: str):
    try:
        d = Path("debug_crops")
        d.mkdir(exist_ok=True)
        img.save(d / f"{name}.png")
    except Exception:
        pass
# Есть предположение что он лишний раз обрабатывает изображения.Область с екстом дополнительно ещё раз обрабатывает хотя это не нужно. Нужно короче пересмотреть эту часть.