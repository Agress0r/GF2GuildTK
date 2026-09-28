"""Regression checks for data loss and integration failures found by the audit."""

import csv
import os
from contextlib import closing
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from config import settings_manager
from core import sheets_sync
from db import database, backups


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    settings = {**settings_manager.DEFAULT_SETTINGS, "db_path": str(tmp_path / "tracker.db")}
    monkeypatch.setattr(settings_manager, "CONFIG_PATH", tmp_path / "settings.json")
    settings_manager.save_settings(settings)
    monkeypatch.setattr(database, "load_settings", lambda: settings)
    database.init_db()
    assert settings_manager.load_settings()["db_path"] == settings["db_path"]
    season = database.create_season(1, "Audit")
    database.save_score(season, "Alice", 100, 1, day_number=1)
    return season, settings


class Sheet:
    id = 123
    title = "Audit"

    def __init__(self, players):
        self.rows = [["Date"], ["Total"], *players]
        self.writes = []

    def get_all_values(self):
        return [row[:] for row in self.rows]

    def update_cells(self, cells, **kwargs):
        self.writes.extend(cells)


class Client:
    def __init__(self, sheet):
        self.sheet = sheet
        self.ids = []

    def open_by_key(self, key):
        return self

    def get_worksheet(self, index):
        return self.sheet

    def get_worksheet_by_id(self, sheet_id):
        self.ids.append(sheet_id)
        return self.sheet


def connect(monkeypatch, rows):
    sheet = Sheet(rows)
    client = Client(sheet)
    monkeypatch.setattr(sheets_sync, "authenticate", lambda _: client)
    return sheet, client


def test_export_tracks_sheet_identity_and_player_row(local_db, monkeypatch):
    season, _ = local_db
    sheet, client = connect(monkeypatch, [["Alice", "90"]])
    preview = sheets_sync.prepare_sync("key", "book", season, worksheet_id=123)
    sheet.rows[2][0] = "Bob"
    with pytest.raises(ValueError, match="после предпросмотра"):
        sheets_sync.execute_sync(preview)
    assert sheet.writes == []
    assert client.ids == [123, 123]


@pytest.mark.parametrize("prepare", [sheets_sync.prepare_sync, sheets_sync.prepare_reverse_sync])
@pytest.mark.parametrize("names", [["Alice", "Alice"], ["Alice", "Alicee"]])
def test_duplicate_and_ambiguous_players_rejected(local_db, monkeypatch, prepare, names):
    connect(monkeypatch, [[name, "90"] for name in names])
    with pytest.raises(ValueError):
        prepare("key", "book", local_db[0])


@pytest.mark.parametrize("value,expected", [("1,000", 1000), ("1\u00a0000", 1000), ("1\u202f000", 1000), ("1000", 1000)])
def test_integer_formats(local_db, monkeypatch, value, expected):
    connect(monkeypatch, [["Alice", value]])
    preview = sheets_sync.prepare_reverse_sync("key", "book", local_db[0])
    assert preview.diffs[0].sheet_deltas[1] == expected


@pytest.mark.parametrize("value", ["1,5", "1.5", "-5", "#VALUE!", "1 00"])
def test_bad_values_never_silently_change_scores(local_db, monkeypatch, value):
    connect(monkeypatch, [["Alice", value]])
    with pytest.raises(ValueError):
        sheets_sync.prepare_reverse_sync("key", "book", local_db[0])
    assert database.get_scores_for_season(local_db[0])[0]["total_score"] == 100


def test_ocr_atomic_validation_later_total_and_undo(local_db):
    season, settings = local_db
    database.save_score(season, "Alice", 500, 1, day_number=3)
    with pytest.raises(ValueError):
        database.import_ocr_scores(season, 2, [("Alice", 200), ("Bob", -1)])
    assert database.get_scores_for_season(season)[0]["day_snapshots"] == {d: {1: 100, 3: 500}.get(d) for d in range(1, 8)}
    database.import_ocr_scores(season, 2, [("Alice", 200), ("Alicee", 50)])
    rows = {row["name"]: row for row in database.get_scores_for_season(season)}
    assert rows["Alice"]["total_score"] == 500
    assert rows["Alicee"]["total_score"] == 50  # preserve reviewed similar names
    backups.restore_latest_import(settings["db_path"])
    rows = database.get_scores_for_season(season)
    assert len(rows) == 1
    assert rows[0]["day_snapshots"] == {d: {1: 100, 3: 500}.get(d) for d in range(1, 8)}


def test_backups_are_isolated_per_database(local_db, tmp_path):
    season, settings = local_db
    database.import_ocr_scores(season, 2, [("Alice", 200)])
    assert backups.latest_import(settings["db_path"]) is not None
    assert backups.latest_import(tmp_path / "another.db") is None
    with pytest.raises(ValueError):
        backups.restore_latest_import(tmp_path / "another.db")


def test_google_import_into_new_season_reuses_global_players(local_db, monkeypatch):
    connect(monkeypatch, [["Alice", "100", "50"]])
    target = database.create_season(2, "Next")
    preview = sheets_sync.prepare_reverse_sync("key", "book", target)
    assert preview.new_players_count == 0
    assert sheets_sync.execute_reverse_sync(preview).imported == 1
    assert len(database.get_all_player_names_with_lock()) == 1
    assert database.get_scores_for_season(target)[0]["total_score"] == 150


def test_csv_unicode_empty_days_and_write_error(local_db, tmp_path):
    from PyQt6.QtWidgets import QApplication, QFileDialog
    from ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    database.import_ocr_scores(local_db[0], 1, [('Игрок, "猫"', 1234)])
    window = MainWindow()
    window._current_season_id = local_db[0]
    output = tmp_path / "scores.csv"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(output), "")):
        window._export_csv()
    with output.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    row = next(row for row in rows if row[1] == 'Игрок, "猫"')
    assert row[2:] == ["1234", "", "", "", "", "", "", "1234"]
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(tmp_path), "")), patch("ui.main_window.mb_critical") as error:
        window._export_csv()
        error.assert_called_once()
    window.close()


def test_ocr_transaction_rolls_back_on_database_error(local_db):
    season, _ = local_db
    with closing(database.get_connection()) as conn:
        conn.execute("CREATE TRIGGER fail_bob BEFORE INSERT ON players WHEN NEW.name='Bob' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        conn.commit()
    with pytest.raises(Exception, match="test failure"):
        database.import_ocr_scores(season, 2, [("Alice", 200), ("Bob", 300)])
    assert database.get_scores_for_season(season)[0]["day_snapshots"] == {d: 100 if d == 1 else None for d in range(1, 8)}


def test_shared_spin_arrows_and_contrast(local_db):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from ui.controls import AppSpinBox, AppDoubleSpinBox
    from ui.theme import DIALOG_STYLE, style_for, theme_color

    app = QApplication.instance() or QApplication([])
    settings = local_db[1]
    settings.update(high_contrast=True, font_scale=1.6)
    settings_manager.save_settings(settings)
    assert theme_color("#a4a094").name() == "#eeeeee"
    for cls in (AppSpinBox, AppDoubleSpinBox):
        spin = cls()
        spin.setStyleSheet(style_for(DIALOG_STYLE))
        spin.resize(160, 50)
        spin.setValue(5)
        spin.show()
        app.processEvents()
        QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=QPoint(spin.width() - 10, spin.height() // 4))
        assert spin.value() == 6
        QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=QPoint(spin.width() - 10, 3 * spin.height() // 4))
        assert spin.value() == 5
        spin.close()


def test_clipboard_image_and_reviewed_ocr_save(local_db):
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QApplication
    from core.manual_processor import OcrResult
    from ui.manual_mode_dialog import ManualModeDialog

    app = QApplication.instance() or QApplication([])
    dialog = ManualModeDialog(local_db[0], "Audit")
    app.clipboard().clear()
    dialog._paste_clipboard()
    assert not dialog._images
    image = QImage(100, 60, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    app.clipboard().setImage(image)
    dialog._paste_clipboard()
    assert dialog._images[0].size == (100, 60)
    dialog._on_result_row(OcrResult("ReviewedPlayer", 250, 0.99, score_variants={250}))
    with patch("ui.manual_mode_dialog.mb_info"), patch("ui.manual_mode_dialog.mb_question", return_value=True):
        dialog._save_to_db()
    assert any(row["name"] == "ReviewedPlayer" and row["total_score"] == 250
               for row in database.get_scores_for_season(local_db[0]))
    dialog.close()
    app.clipboard().clear()
