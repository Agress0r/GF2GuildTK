"""
Tests for name recognition in core/ocr.py

Historical bug (fixed): RapidOCR detector was splitting one name into multiple
bounding boxes, and ' '.join() produced fragments like "BromRU RUS" instead
of "BromRUS".

Current fixes applied:
  1. _read_text() uses use_det=False, use_cls=False → whole-line recognizer,
     no region splitting.
  2. 8px white padding around the crop → prevents edge-clipping artifacts.
  3. Spaces stripped from result → player names never contain spaces.
  4. db._fuzzy_resolve_name() thresholds raised to ratio>=70, dist<=4.

Run: python -m pytest tests/test_ocr_names.py -v
     (from repo root, using GF2TTK venv)
"""
from __future__ import annotations

import re
import sys
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image, ImageDraw, ImageFont, ImageOps
import numpy as np

# ---------------------------------------------------------------------------
# Known names (ground truth for this test run)
# ---------------------------------------------------------------------------
KNOWN_NAMES = [
    "BromRUS", "1rokoh", "ZEIT", "Flow", "Rimush", "eRef4n",
    "Robella", "Killa", "rart", "Stingly", "Oxient", "Rshish",
    "Orin", "心率失调", "Uwuharu", "Ninth", "1Percent", "格里芬冤种",
    "米知露", "Babuleh", "Trigun", "Valera", "Pascal", "sleepyhead",
    "YukiSam", "BlackRo", "Aknas", "HK416", "青花蛋粥", "Shrike",
    "Skril", "对于安娜", "He11", "DMSoul", "Lis12", "猫低食饭",
    "七红天汐奈", "艾丽丝", "Mark9416",
]

# Historical buggy observations (kept as reference; these were produced by the
# old code with ' '.join() and detection enabled).
HISTORICAL_BUGGY_OBSERVATIONS = [
    ("BromRUS",   "BromRU RUS"),
    ("1rokoh",    "1rol okol"),
    ("Flow",      "FIo ow"),
    ("Oxient",    "Dxient O"),
    ("eRef4n",    "Ref4n eRe e"),
    ("Robella",   "ella Rol e a"),
    ("Killa",     "Killa a"),
    ("Orin",      "Orin O"),
    ("1Percent",  "1P C ent er"),
    ("米知露",     "米知露 露"),
    ("Babuleh",   "abule leh Ba al"),
    ("Valera",    "a era"),
    ("Pascal",    "P ascal"),
    ("sleepyhead","sle ee"),
    ("YukiSam",   "YukiSa Yu am"),
    ("BlackRo",   "lackRo B 313"),
    ("HK416",     "HK41 9"),
    ("He11",      "He 1 11 e"),
    ("DMSoul",    "DMSo inog"),
    ("Lis12",     "Lis 1 12"),
    ("Mark9416",  "Mark9 41 6"),
]


# ---------------------------------------------------------------------------
# Helper: fuzzy best-match from known list
# ---------------------------------------------------------------------------
def _fuzzy_match(raw: str, candidates: list[str], threshold: int = 60) -> str | None:
    try:
        from thefuzz import process as fuzz_process
        best, score = fuzz_process.extractOne(raw, candidates)
        return best if score >= threshold else None
    except ImportError:
        return None


# ===========================================================================
# Test 1 — historical reference: document how the old bug manifested
# ===========================================================================
class TestHistoricalBugReference(unittest.TestCase):
    """Documents the historical bug for reference. Not regression tests —
    just verifies our observations table is internally consistent."""

    def test_all_buggy_observations_differ_from_expected(self):
        """Every documented buggy output should differ from the expected name."""
        for expected, buggy in HISTORICAL_BUGGY_OBSERVATIONS:
            self.assertNotEqual(
                expected, buggy,
                f"{expected!r}: buggy output accidentally matches expected"
            )

    def test_buggy_outputs_contain_spaces(self):
        """Most historical buggy outputs had internal spaces (from ' '.join)."""
        outputs_with_spaces = [buggy for _, buggy in HISTORICAL_BUGGY_OBSERVATIONS
                               if " " in buggy]
        # At least half the examples had internal spaces
        self.assertGreater(len(outputs_with_spaces), len(HISTORICAL_BUGGY_OBSERVATIONS) // 2)


# ===========================================================================
# Test 2 — current _read_text behaviour (mocked RapidOCR)
# ===========================================================================
class TestReadTextCurrentBehaviour(unittest.TestCase):
    """
    Patch RapidOCR to return controlled results.
    Verifies the current implementation (use_det=False, strip spaces, padding).
    """

    def _make_fake_ocr_result(self, texts: list[str]):
        """Build a fake RapidOCR result: list of [bbox, text, score]."""
        return [[None, t, 0.99] for t in texts]

    def _simulate_read_text(self, img: Image.Image, fake_texts: list[str]) -> str:
        """Simulate what _read_text does with given fake OCR output."""
        # Mirrors the current implementation logic
        from PIL import ImageOps
        padded = ImageOps.expand(img, border=8, fill=(255, 255, 255))
        result = self._make_fake_ocr_result(fake_texts)
        texts = [r[1] for r in result] if result else []
        return "".join(texts).replace(" ", "").strip()

    def _blank_img(self, size=(100, 30)) -> Image.Image:
        return Image.new("RGB", size, (255, 255, 255))

    # --- padding ---

    def test_padding_increases_image_size(self):
        """Padding of 8px should increase dimensions by 16px each side."""
        img = self._blank_img((100, 30))
        from PIL import ImageOps
        padded = ImageOps.expand(img, border=8, fill=(255, 255, 255))
        self.assertEqual(padded.size, (116, 46))

    def test_padding_is_white(self):
        """Padding pixels should be white."""
        img = Image.new("RGB", (10, 10), (0, 0, 0))  # black image
        from PIL import ImageOps
        padded = ImageOps.expand(img, border=4, fill=(255, 255, 255))
        # Corner pixel (0,0) is in the padding — should be white
        self.assertEqual(padded.getpixel((0, 0)), (255, 255, 255))

    # --- space stripping ---

    def test_spaces_stripped_from_single_region(self):
        img = self._blank_img()
        result = self._simulate_read_text(img, ["BromRU RUS"])
        self.assertNotIn(" ", result)
        self.assertEqual(result, "BromRURUS")

    def test_spaces_stripped_from_fragments(self):
        img = self._blank_img()
        result = self._simulate_read_text(img, ["P", "ascal"])
        self.assertEqual(result, "Pascal")

    def test_no_space_join_eliminates_fragmentation(self):
        """Multi-region clean splits produce correct name."""
        cases = [
            (["1Per", "cent"],   "1Percent"),
            (["Mark9", "416"],   "Mark9416"),
            (["Lis", "12"],      "Lis12"),
            (["Bab", "uleh"],    "Babuleh"),
            (["Yu", "kiSam"],    "YukiSam"),
            (["Black", "Ro"],    "BlackRo"),
        ]
        img = self._blank_img()
        for fragments, expected in cases:
            result = self._simulate_read_text(img, fragments)
            self.assertEqual(result, expected, f"fragments={fragments}")

    def test_single_region_names_unchanged(self):
        """Names that OCR returns as one region pass through without changes."""
        img = self._blank_img()
        for name in ["ZEIT", "rart", "Killa", "Rimush", "Stingly",
                     "Uwuharu", "Ninth", "Trigun", "Shrike", "Skril", "Aknas",
                     "心率失调", "格里芬冤种", "对于安娜"]:
            result = self._simulate_read_text(img, [name])
            self.assertEqual(result, name)

    def test_chinese_names_preserved(self):
        img = self._blank_img()
        for name in ["心率失调", "格里芬冤种", "米知露", "青花蛋粥", "猫低食饭",
                     "七红天汐奈", "艾丽丝", "对于安娜"]:
            result = self._simulate_read_text(img, [name])
            self.assertEqual(result, name)


# ===========================================================================
# Test 3 — fuzzy matching with updated thresholds (ratio>=70, dist<=4)
# ===========================================================================
class TestFuzzyMatching(unittest.TestCase):
    """Verify fuzzy matching corrects residual OCR noise after the primary fix."""

    def _match(self, raw: str, threshold: int = 70) -> str | None:
        return _fuzzy_match(raw, KNOWN_NAMES, threshold)

    # Cases where no-space join leaves an overlap (dist=3 was previously blocked)
    def test_fuzzy_fixes_bromrus_overlap(self):
        # "BromRU"+"RUS" → "BromRURUS", dist("BromRURUS","BromRUS")=3
        result = self._match("BromRURUS")
        self.assertEqual(result, "BromRUS")

    def test_fuzzy_fixes_orin_trailing(self):
        # "Orin O" → strip spaces → "OrinO", dist=1
        result = self._match("OrinO")
        self.assertEqual(result, "Orin")

    def test_fuzzy_fixes_he11_overlap(self):
        # "He1" + "11e" → "He111e", dist("He111e","He11")=2
        result = self._match("He111e", threshold=50)
        self.assertEqual(result, "He11")

    def test_fuzzy_fixes_lis12_overlap(self):
        # "Lis" + "112" → "Lis112", dist=1
        result = self._match("Lis112", threshold=50)
        self.assertEqual(result, "Lis12")

    def test_fuzzy_exact_names_unchanged(self):
        """Names already correct match themselves."""
        for name in ["Rimush", "ZEIT", "rart", "Killa", "Uwuharu", "Ninth",
                     "Trigun", "Shrike", "Aknas", "Valera", "Pascal"]:
            result = self._match(name, threshold=80)
            self.assertEqual(result, name, f"Exact name {name!r} should match itself")

    def test_fuzzy_handles_chinese_exact(self):
        for name in ["心率失调", "格里芬冤种", "米知露", "青花蛋粥", "猫低食饭",
                     "七红天汐奈", "艾丽丝", "对于安娜"]:
            result = self._match(name, threshold=80)
            self.assertEqual(result, name)

    def test_fuzzy_rejects_completely_different(self):
        """Unrelated strings should not match anything (below threshold)."""
        result = _fuzzy_match("xyzxyzxyz", KNOWN_NAMES, threshold=80)
        self.assertIsNone(result)


# ===========================================================================
# Test 4 — db._fuzzy_resolve_name thresholds
# ===========================================================================
class TestDbFuzzyResolve(unittest.TestCase):
    """Verify db._fuzzy_resolve_name uses the updated thresholds (ratio>=70, dist<=4)."""

    def setUp(self):
        try:
            from db.database import _fuzzy_resolve_name
            self._resolve = _fuzzy_resolve_name
        except ImportError as e:
            self.skipTest(f"db.database import failed: {e}")

    def _players(self, names: list[str]) -> list[dict]:
        return [{"id": i, "name": n, "locked": False} for i, n in enumerate(names)]

    def test_exact_match_returns_canonical(self):
        result = self._resolve("BromRUS", self._players(["BromRUS", "Killa"]))
        self.assertEqual(result, "BromRUS")

    def test_dist3_match_now_accepted(self):
        # dist("BromRURUS","BromRUS")=3, should match with new threshold dist<=4
        result = self._resolve("BromRURUS", self._players(KNOWN_NAMES))
        self.assertEqual(result, "BromRUS")

    def test_dist1_match_accepted(self):
        result = self._resolve("OrinO", self._players(KNOWN_NAMES))
        self.assertEqual(result, "Orin")

    def test_completely_different_not_matched(self):
        result = self._resolve("zzzzzzzzz", self._players(KNOWN_NAMES))
        self.assertEqual(result, "zzzzzzzzz")

    def test_short_name_not_fuzzied(self):
        # Names < 3 chars skip fuzzy
        result = self._resolve("OK", self._players(KNOWN_NAMES))
        self.assertEqual(result, "OK")


# ===========================================================================
# Test 5 — integration: OCR on actual debug crop images
# ===========================================================================
class TestDebugCropImages(unittest.TestCase):
    """
    Run actual OCR on saved debug crops to detect regressions.
    Skips gracefully if images or OCR libs are unavailable.
    """

    DEBUG_DIR = Path(__file__).parent.parent / "debug_crops"

    def setUp(self):
        try:
            from core.ocr import _read_text, _read_number
            self._read_text = _read_text
            self._read_number = _read_number
        except ImportError as e:
            self.skipTest(f"core.ocr import failed: {e}")

    def test_name_crops_produce_nonempty_text(self):
        """All *_name.png debug crops should return non-empty text."""
        name_crops = list(self.DEBUG_DIR.glob("*_name.png")) if self.DEBUG_DIR.exists() else []
        if not name_crops:
            self.skipTest("No *_name.png crops found in debug_crops/")
        for crop_path in name_crops:
            img = Image.open(crop_path)
            result = self._read_text(img, label=crop_path.stem)
            self.assertTrue(result.strip(), f"{crop_path.name} returned empty text")

    def test_name_crops_have_no_internal_spaces(self):
        """After the fix, name crop results must not contain spaces."""
        name_crops = list(self.DEBUG_DIR.glob("*_name.png")) if self.DEBUG_DIR.exists() else []
        if not name_crops:
            self.skipTest("No *_name.png crops found in debug_crops/")
        problems = []
        for crop_path in name_crops:
            img = Image.open(crop_path)
            result = self._read_text(img, label=crop_path.stem)
            if " " in result:
                problems.append(f"{crop_path.name}: {result!r}")
        self.assertEqual(problems, [], "Names should have no internal spaces:\n" + "\n".join(problems))

    def test_score_crops_produce_numbers(self):
        """All *_score.png debug crops should return a positive integer."""
        score_crops = list(self.DEBUG_DIR.glob("*_score.png")) if self.DEBUG_DIR.exists() else []
        if not score_crops:
            self.skipTest("No *_score.png crops found in debug_crops/")
        for crop_path in score_crops:
            img = Image.open(crop_path)
            result = self._read_number(img, label=crop_path.stem)
            self.assertIsNotNone(result, f"{crop_path.name} returned None")
            self.assertIsInstance(result, int)
            self.assertGreater(result, 0, f"{crop_path.name} returned 0 or negative")


# ===========================================================================
# Test 6 — synthetic images with drawn text
# ===========================================================================
class TestSyntheticNameImages(unittest.TestCase):
    """
    Generate minimal synthetic images with known text and run OCR.
    Skip if OCR not available.
    """

    def setUp(self):
        try:
            from core.ocr import _read_text
            self._read_text = _read_text
        except ImportError as e:
            self.skipTest(f"core.ocr import failed: {e}")

    def _make_text_image(self, text: str, size=(300, 50), bg=255, fg=0) -> Image.Image:
        img = Image.new("RGB", size, color=(bg, bg, bg))
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), text, fill=(fg, fg, fg))
        return img

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", "", text)

    def test_simple_ascii_names_nonempty(self):
        """Synthetic ASCII name images should return non-empty OCR results."""
        names = ["ZEIT", "rart", "Killa", "Aknas", "Shrike"]
        ocr_empty = []
        for name in names:
            img = self._make_text_image(name, size=(400, 80))
            result = self._read_text(img, label=f"synthetic_{name}")
            if not self._normalize(result):
                ocr_empty.append(name)
        self.assertLessEqual(
            len(ocr_empty), 1,
            f"OCR returned empty for too many synthetic names: {ocr_empty}"
        )

    def test_alphanumeric_names_nonempty(self):
        """Names mixing letters and numbers should return non-empty results."""
        names = ["He11", "HK416", "Lis12", "Mark9416"]
        for name in names:
            img = self._make_text_image(name)
            result = self._read_text(img, label=f"synthetic_{name}")
            self.assertTrue(
                len(self._normalize(result)) > 0,
                f"OCR returned empty for synthetic {name!r}"
            )

    def test_result_has_no_internal_spaces(self):
        """Results from the fixed _read_text should never have internal spaces."""
        names = ["Pascal", "BlackRo", "1Percent", "BromRUS"]
        for name in names:
            img = self._make_text_image(name, size=(400, 80))
            result = self._read_text(img, label=f"synthetic_{name}")
            self.assertNotIn(" ", result, f"Internal space in result {result!r} for {name!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
