"""Behavior checks for reviewed sync, backups, and OCR evidence."""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core import manual_processor, sheets_sync
from config import settings_manager
from db import database
from db.backups import latest_import, restore_latest_import


class _Sheet:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []

    def get_all_values(self):
        return [row[:] for row in self.rows]

    def update_cells(self, cells, value_input_option):
        self.writes.extend(cells)
        for cell in cells:
            row = self.rows[cell.row - 1]
            while len(row) < cell.col:
                row.append("")
            row[cell.col - 1] = str(cell.value)


class _Client:
    def __init__(self, sheet):
        self.sheet = sheet

    def open_by_key(self, _key):
        return self

    def get_worksheet(self, _index):
        return self.sheet


class ImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "tracker.db"
        self.settings = {"db_path": str(self.db_path)}
        self.db_patch = patch.object(database, "load_settings", return_value=self.settings)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        database.init_db()
        self.season = database.create_season(1, "Guild")
        database.save_score(self.season, "Alice", 100, 1, day_number=1)

    def _sheet(self, row):
        sheet = _Sheet([["Дата"], ["Всего очков"], row])
        auth = patch.object(sheets_sync, "authenticate", return_value=_Client(sheet))
        auth.start()
        self.addCleanup(auth.stop)
        return sheet

    def test_reverse_sync_cell_choice_backup_and_undo(self):
        self._sheet(["Alice", "90", "50"])
        preview = sheets_sync.prepare_reverse_sync("creds", "sheet", self.season)
        diff = preview.diffs[0]
        diff.choices[1] = "db"
        self.assertEqual(preview.changed_count, 1)
        result = sheets_sync.execute_reverse_sync(preview)
        self.assertEqual(result.imported, 1)
        with closing(database.get_connection()) as conn:
            rows = conn.execute("SELECT day_number,total_score FROM day_scores ORDER BY day_number").fetchall()
            source = conn.execute("SELECT source FROM change_history ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertEqual([(r[0], r[1]) for r in rows], [(1, 100), (2, 140)])
        self.assertEqual(source, "google")
        self.assertEqual(latest_import(self.db_path)["source"], "google")
        restore_latest_import(self.db_path)
        with closing(database.get_connection()) as conn:
            rows = conn.execute("SELECT day_number,total_score FROM day_scores ORDER BY day_number").fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(1, 100)])

    def test_undo_refuses_later_changes(self):
        self._sheet(["Alice", "110"])
        preview = sheets_sync.prepare_reverse_sync("creds", "sheet", self.season)
        sheets_sync.execute_reverse_sync(preview)
        database.save_score(self.season, "Alice", 200, 1, day_number=2)
        with self.assertRaises(ValueError):
            restore_latest_import(self.db_path)

    def test_export_respects_choice_and_checks_staleness(self):
        sheet = self._sheet(["Alice", "90", "40"])
        preview = sheets_sync.prepare_sync("creds", "sheet", self.season)
        preview.diffs[0].choices[2] = "sheet"
        result = sheets_sync.execute_sync(preview)
        self.assertEqual(result.updated, 1)
        self.assertEqual(sheet.rows[2][1:3], ["100", "40"])
        preview = sheets_sync.prepare_sync("creds", "sheet", self.season)
        sheet.rows[2][2] = "external edit"
        with self.assertRaises(ValueError):
            sheets_sync.execute_sync(preview)

    def test_reverse_sync_checks_staleness_and_bad_values(self):
        sheet = self._sheet(["Alice", "110"])
        preview = sheets_sync.prepare_reverse_sync("creds", "sheet", self.season)
        database.save_score(self.season, "Alice", 120, 1, day_number=1)
        with self.assertRaises(ValueError):
            sheets_sync.execute_reverse_sync(preview)
        sheet.rows[2][1] = "garbage"
        with self.assertRaises(ValueError):
            sheets_sync.prepare_reverse_sync("creds", "sheet", self.season)

    def test_reverse_sync_uses_selected_worksheet_id(self):
        first = _Sheet([["Дата"], ["Всего очков"], ["Alice", "90"]])
        last = _Sheet([["Дата"], ["Всего очков"], ["Alice", "175"]])
        first.id, first.title = 101, "Прошлый сезон"
        last.id, last.title = 202, "Текущий сезон"

        class Spreadsheet:
            def open_by_key(self, _key):
                return self

            def worksheets(self, exclude_hidden=False):
                assert exclude_hidden
                return [first, last]

            def get_worksheet_by_id(self, worksheet_id):
                return {101: first, 202: last}[worksheet_id]

        with patch.object(sheets_sync, "authenticate", return_value=Spreadsheet()):
            self.assertEqual(
                sheets_sync.list_import_worksheets("creds", "sheet"),
                [(101, "Прошлый сезон"), (202, "Текущий сезон")],
            )
            preview = sheets_sync.prepare_reverse_sync(
                "creds", "sheet", self.season, worksheet_id=202,
            )

        self.assertEqual(preview.worksheet_id, 202)
        self.assertEqual(preview.worksheet_title, "Текущий сезон")
        self.assertEqual(preview.diffs[0].sheet_snapshots[1], 175)

    def test_reverse_sync_ignores_template_zeroes_after_last_active_day(self):
        database.save_day_score(self.season, 1, 1, 41325)
        database.save_day_score(self.season, 1, 2, 82650)
        database.save_day_score(self.season, 1, 3, 82650)
        for day in range(4, 8):
            database.save_day_score(self.season, 1, day, 41325)
        database.save_score(self.season, "Flow", 27550, 2, day_number=1)
        flow_id = next(row["player_id"] for row in database.get_scores_for_season(self.season)
                       if row["name"] == "Flow")
        database.save_day_score(self.season, flow_id, 2, 55100)
        database.save_day_score(self.season, flow_id, 3, 82650)

        sheet = _Sheet([
            ["Дата", "24.09", "25.09", "26.09", "27.09", "28.09", "29.09", "30.09"],
            ["Всего очков", "68875", "68875", "27550", "0", "0", "0", "0"],
            ["Alice", "41325", "41325", "0", "0", "0", "0", "0"],
            ["Flow", "27550", "27550", "27550", "0", "0", "0", "0"],
        ])
        with patch.object(sheets_sync, "authenticate", return_value=_Client(sheet)):
            preview = sheets_sync.prepare_reverse_sync("creds", "sheet", self.season)

        alice = next(diff for diff in preview.diffs if diff.sheet_name == "Alice")
        self.assertEqual(preview.active_day, 3)
        self.assertTrue(alice.sheet_present[3])
        self.assertFalse(alice.sheet_present[4])
        self.assertEqual(alice.sheet_snapshots[3], 82650)
        self.assertEqual(alice.selected_snapshot(4), 41325)
        alice.choices[4] = "sheet"
        self.assertEqual(alice.selected_snapshot(4), 41325)
        self.assertEqual(preview.changed_count, 0)

    def test_ocr_keeps_confidence_and_duplicate_evidence(self):
        image = Image.new("RGB", (10, 10))
        offsets = {"name": {"dx": 0, "dy": 0, "w": 2, "h": 2},
                   "score": {"dx": 0, "dy": 0, "w": 2, "h": 2}}
        with patch.object(manual_processor, "load_templates", return_value=[object()]), \
             patch.object(manual_processor, "find_badges", return_value=[(0, 0, 2, 2)]), \
             patch.object(manual_processor, "_read_text_with_confidence", side_effect=[("Alice", .6), ("Alice", .9)]), \
             patch.object(manual_processor, "_read_number", side_effect=[100, 150]):
            rows = manual_processor.process_images([image, image], offsets)
        self.assertEqual((rows[0].score, rows[0].confidence, rows[0].occurrences), (150, .9, 2))
        self.assertEqual(rows[0].score_variants, {100, 150})

    def test_calibration_profiles_are_separate(self):
        with patch.object(settings_manager, "CONFIG_PATH", Path(self.tmp.name) / "settings.json"):
            settings_manager.save_badge_offsets({"name": {"dx": 1}}, 1920, 1080, 100)
            settings_manager.save_badge_offsets({"name": {"dx": 2}}, 1920, 1080, 125)
            self.assertEqual(settings_manager.get_badge_offsets(1920, 1080, 100)["name"]["dx"], 1)
            self.assertEqual(settings_manager.get_badge_offsets(1920, 1080, 125)["name"]["dx"], 2)
            self.assertIsNone(settings_manager.get_badge_offsets(2560, 1440, 100))


if __name__ == "__main__":
    unittest.main()
