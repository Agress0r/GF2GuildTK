"""Offscreen smoke checks for the redesigned preview and OCR draft dialogs."""

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from core.manual_processor import OcrResult
from core.sheets_sync import PlayerDiff, PlayerImportDiff, ReversePreview, SyncPreview
from db import database
from ui.manual_mode_dialog import ManualModeDialog
from ui.controls import AppComboBox, ComparisonChoice
from ui.network_capture_wait_dialog import NetworkCaptureWaitDialog
from ui.network_import_dialog import NetworkImportDialog
from ui.settings_dialog import SettingsDialog
from core.network_capture import NetworkPlayer, NetworkSnapshot
from ui.sheets_import_preview_dialog import SheetsImportPreviewDialog
from ui.sheets_tab_dialog import SheetsTabDialog
from ui.sheets_preview_dialog import SheetsPreviewDialog


class UiImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_google_preview_choices(self):
        diff = PlayerDiff("Alice", "Alice", 3,
                          {d: "27550" if d == 1 else "0" for d in range(1, 8)},
                          {d: 41325 if d == 1 else 0 for d in range(1, 8)}, True)
        preview = SyncPreview([diff], [], [], [], "creds", "sheet", 0)
        dialog = SheetsPreviewDialog(preview)
        dialog.show()
        self.app.processEvents()
        choice = dialog._table.cellWidget(0, 1)
        self.assertIsInstance(choice, ComparisonChoice)
        self.assertGreaterEqual(dialog._table.rowHeight(0), 70)
        self.assertIn("41 325", choice._labels["db"].text())
        self.assertIn("27 550", choice._labels["sheet"].text())
        for label in choice._labels.values():
            self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        combo = choice.combo
        self.assertIsInstance(combo, AppComboBox)
        self.assertGreaterEqual(combo.height(), 34)
        self.assertTrue(all(
            combo.fontMetrics().horizontalAdvance(combo.itemText(index)) <= combo.width() - 30
            for index in range(combo.count())
        ))
        QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=combo.rect().center())
        self.app.processEvents()
        self.assertTrue(combo.view().isVisible())
        second_item = combo.view().visualRect(combo.model().index(1, 0)).center()
        QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=second_item)
        self.app.processEvents()
        self.assertEqual(choice.currentData(), "sheet")
        self.assertEqual(preview.changed_count, 0)
        self.assertFalse(dialog._sync_btn.isEnabled())
        dialog.close()

        reverse_diff = PlayerImportDiff(
            "Alice", "Alice", False,
            {d: 10 if d == 1 else 0 for d in range(1, 8)},
            {d: 10 for d in range(1, 8)},
            {d: 5 for d in range(1, 8)}, True,
        )
        reverse = ReversePreview([reverse_diff], "creds", "sheet", 0, 1)
        dialog = SheetsImportPreviewDialog(reverse)
        for day in range(1, 8):
            choice = dialog._table.cellWidget(0, day)
            self.assertIsInstance(choice, ComparisonChoice)
            choice.setCurrentIndex(1)
        self.assertEqual(reverse.changed_count, 0)
        self.assertFalse(dialog._import_btn.isEnabled())
        dialog.close()

        new_player = PlayerImportDiff(
            "Bob", None, True,
            {d: 10 if d == 1 else 0 for d in range(1, 8)},
            {d: 10 for d in range(1, 8)},
            {d: None for d in range(1, 8)}, True,
            sheet_present={d: d == 1 for d in range(1, 8)},
            choices={d: "db" for d in range(2, 8)},
        )
        reverse = ReversePreview([new_player], "creds", "sheet", 0, 1)
        dialog = SheetsImportPreviewDialog(reverse)
        self.assertEqual(reverse.new_players_count, 1)
        dialog._table.cellWidget(0, 1).setCurrentIndex(1)
        self.assertEqual(reverse.new_players_count, 0)
        self.assertFalse(dialog._import_btn.isEnabled())
        dialog.close()

    def test_import_tab_selection_remembers_choice_and_shows_source(self):
        tabs = [(101, "Старый лист"), (202, "Текущий лист")]
        dialog = SheetsTabDialog(tabs, None, "Очки клана")
        self.assertEqual(dialog.worksheet_id, 202)
        dialog.close()
        dialog = SheetsTabDialog(tabs, 101, "Очки клана")
        self.assertEqual(dialog.worksheet_id, 101)
        dialog.close()

        preview = ReversePreview([], "creds", "sheet", 0, 1,
                                 worksheet_id=202, worksheet_title="Текущий лист")
        preview_dialog = SheetsImportPreviewDialog(preview)
        labels = [label.text() for label in preview_dialog.findChildren(QLabel)]
        self.assertIn("Источник: лист «Текущий лист»", labels)
        preview_dialog.close()

    def test_import_preview_hides_future_template_zeroes(self):
        diff = PlayerImportDiff(
            "Alice", "Alice", False,
            {1: 41325, 2: 41325, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0},
            {1: 41325, 2: 82650, 3: 82650, 4: 82650, 5: 82650, 6: 82650, 7: 82650},
            {1: 41325, 2: 82650, 3: 82650, 4: 41325, 5: 41325, 6: 41325, 7: 41325},
            False,
            sheet_present={day: day <= 3 for day in range(1, 8)},
            choices={day: "db" for day in range(4, 8)},
        )
        preview = ReversePreview([diff], "creds", "sheet", 0, 1, active_day=3)
        dialog = SheetsImportPreviewDialog(preview)
        self.assertIsNone(dialog._table.cellWidget(0, 4))
        self.assertEqual(dialog._table.item(0, 4).text(), "—")
        self.assertEqual(preview.changed_count, 0)
        self.assertFalse(dialog._import_btn.isEnabled())
        dialog.close()

    def test_ocr_draft_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "tracker.db"
            settings = {"db_path": str(db_path), "font_scale": 1.0, "high_contrast": False}
            with patch.object(database, "load_settings", return_value=settings), \
                 patch("ui.manual_mode_dialog.load_settings", return_value=settings):
                database.init_db()
                season = database.create_season(1, "Guild")
                dialog = ManualModeDialog(season, "Guild")
                dialog.show()
                self.app.processEvents()
                self.assertIsInstance(dialog.day_spin, AppComboBox)
                QTest.mouseClick(dialog.day_spin, Qt.MouseButton.LeftButton,
                                 pos=dialog.day_spin.rect().center())
                self.app.processEvents()
                self.assertTrue(dialog.day_spin.view().isVisible())
                dialog.day_spin.hidePopup()
                dialog._add_image(Image.new("RGB", (12, 12)), "image.png")
                dialog._on_result_row(OcrResult("Alice", 100, .5, 2, {90, 100}))
                dialog.result_table.item(0, 2).setText("125")
                dialog.close()
                restored = ManualModeDialog(season, "Guild")
                self.assertEqual(len(restored._images), 1)
                self.assertEqual(restored.result_table.item(0, 2).text(), "125")
                self.assertEqual(restored._results[0].occurrences, 2)
                restored.close()

    def test_other_selectors_share_click_behavior(self):
        settings = {
            "db_path": "tracker.db", "google_sheets_id": "",
            "google_credentials_path": "", "font_scale": 1.0,
            "high_contrast": False,
            "google_sheets_history": [{"id": "sheet-1", "name": "Guild scores"}],
        }
        with patch("ui.settings_dialog.load_settings", return_value=settings):
            dialog = SettingsDialog()
            dialog.show()
            self.app.processEvents()
            combo = dialog.gs_combo
            self.assertIsInstance(combo, AppComboBox)
            QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=combo.rect().center())
            self.app.processEvents()
            self.assertTrue(combo.view().isVisible())
            combo.hidePopup()
            dialog.close()

        with tempfile.TemporaryDirectory() as tmp:
            settings["db_path"] = str(Path(tmp) / "tracker.db")
            with patch.object(database, "load_settings", return_value=settings):
                database.init_db()
                season = database.create_season(1, "Guild")
                snapshot = NetworkSnapshot(
                    Path("mock.pcap"), datetime.now(timezone.utc), "Guild",
                    (NetworkPlayer(42, "New player", 100),),
                )
                dialog = NetworkImportDialog(snapshot, season, "Guild")
                dialog.show()
                self.app.processEvents()
                combo = dialog.unmatched[42]
                self.assertIsInstance(combo, AppComboBox)
                self.assertGreaterEqual(dialog.table.rowHeight(0), combo.height())
                QTest.mouseClick(combo, Qt.MouseButton.LeftButton, pos=combo.rect().center())
                self.app.processEvents()
                self.assertTrue(combo.view().isVisible())
                combo.hidePopup()
                dialog.close()

    def test_network_capture_finishes_automatically(self):
        snapshot = NetworkSnapshot(
            Path("automatic.pcap"), datetime.now(timezone.utc), "Guild",
            (NetworkPlayer(1, "Alice", 100),),
        )

        def fake_capture(_stop_event, on_ready, on_progress):
            on_ready()
            on_progress(2, 100)
            return snapshot

        with patch("ui.network_capture_wait_dialog.diagnose_live_capture",
                   return_value=[(True, "Npcap")]), \
             patch("ui.network_capture_wait_dialog.capture_guild_snapshot",
                   side_effect=fake_capture):
            dialog = NetworkCaptureWaitDialog()
            deadline = time.monotonic() + 2
            while dialog.snapshot is None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIs(dialog.snapshot, snapshot)
            self.assertFalse(hasattr(dialog, "check_button"))
            dialog.close()


if __name__ == "__main__":
    unittest.main()
