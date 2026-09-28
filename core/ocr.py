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
            value = _parse_number(raw)
            _log(log, f"    [dddd {label}] raw={raw!r}  value={value!r}")
            if value is not None:
                return value
            _log(log, f"    [dddd {label}] пусто → пробую RapidOCR")
    except Exception as e:
        _log(log, f"    [dddd {label}] ❌ {e} → пробую RapidOCR")

    # Mixed output such as '10oo' must not silently become 10. Recognize the
    # whole line with the independent model before trying detection.
    text, confidence = _read_text_with_confidence(img, label=label, log=log)
    value = _parse_number(text)
    if value is not None and confidence >= 0.5:
        return value

    # Fallback: RapidOCR with text detection.
    try:
        ocr   = _get_rapid(log)
        if ocr is None:
            _log(log, f"    [rapid {label}] тоже недоступна")
            return None
        result, _ = ocr(_preprocess_np(img))
        texts = [r[1] for r in result] if result else []
        joined = "".join(texts)
        _log(log, f"    [rapid {label}] raw={texts!r}  digits={joined!r}")
        return _parse_number(joined)
    except Exception as e:
        _log(log, f"    [rapid {label}] ❌ {e}")
        return None


# ------------------------------------------------------------------ #
#  Text reader — RapidOCR (ONNX, Chinese + Latin)                    #
# ------------------------------------------------------------------ #

def _parse_number(raw) -> int | None:
    text = str(raw).strip()
    if not re.fullmatch(r"[0-9]+|[0-9]{1,3}(?:[ ,\u00a0\u202f][0-9]{3})+", text):
        return None
    return int(re.sub(r"[ ,\u00a0\u202f]", "", text))


def _read_text(img: Image.Image, label: str = "", log=None) -> str:
    return _read_text_with_confidence(img, label, log)[0]


def _read_text_with_confidence(img: Image.Image, label: str = "", log=None) -> tuple[str, float]:
    try:
        ocr = _get_rapid(log)
        if ocr is None:
            _log(log, f"    [rapid {label}] недоступна")
            return "", 0.0
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
            confidences = [float(r[1] if len(r) == 2 else r[2]) for r in result]
        else:
            texts = []
            confidences = []
        # Player names never contain spaces; remove any spaces the recognizer adds.
        text = "".join(texts).replace(" ", "").strip()
        # A short name in a wide crop can be too small for line recognition.
        # Retry only an empty result, using the foreground bounds against the
        # border colour. This also works for light text on a dark background.
        if not text:
            pixels = np.asarray(img.convert("RGB"), dtype=np.int16)
            border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]))
            background = np.median(border, axis=0).astype(np.int16)
            foreground = np.max(np.abs(pixels - background), axis=2) > 35
            if 0 < foreground.mean() < 0.5:
                ys, xs = np.nonzero(foreground)
                left, top = max(0, int(xs.min()) - 4), max(0, int(ys.min()) - 4)
                right = min(img.width, int(xs.max()) + 5)
                bottom = min(img.height, int(ys.max()) + 5)
                focused = img.crop((left, top, right, bottom)).convert("RGB")
                focused = ImageOps.expand(focused, border=8, fill=tuple(int(v) for v in background))
                retry, _ = ocr(np.array(focused), use_det=False, use_cls=False)
                if retry:
                    texts = [r[0] if len(r) == 2 else r[1] for r in retry]
                    confidences = [float(r[1] if len(r) == 2 else r[2]) for r in retry]
                    text = "".join(texts).replace(" ", "").strip()
        _log(log, f"    [rapid {label}] raw={texts!r}  →  {text!r}")
        confidence = min(confidences) if confidences else 0.0
        return text, confidence
    except Exception as e:
        _log(log, f"    [rapid {label}] ❌ {e}")
        _log(log, f"       {traceback.format_exc().splitlines()[-1]}")
        return "", 0.0


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
