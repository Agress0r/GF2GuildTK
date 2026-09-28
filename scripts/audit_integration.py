r"""Opt-in integration audit. User database/settings are never used for writes.

Run: GF2TTK\Scripts\python.exe scripts\audit_integration.py [--google] [--render]
Google mode creates and deletes only its own temporary worksheet.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--google", action="store_true")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    from config import settings_manager
    from core import ocr, sheets_sync, live_capture
    from core.network_capture import load_guild_snapshot, CaptureDecodeError
    from db import database

    original = settings_manager.load_settings()
    output = ROOT / "build" / "audit"
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    ocr.initialize_ocr()
    assert ocr._get_digit() is not None and ocr._get_rapid() is not None
    report["ocr_models"] = "both initialized"

    with tempfile.TemporaryDirectory(prefix="gf2-audit-") as tmp:
        tmp = Path(tmp)
        settings = {**original, "db_path": str(tmp / "audit.db")}
        with patch.object(settings_manager, "CONFIG_PATH", tmp / "settings.json"), patch.object(database, "load_settings", return_value=settings):
            settings_manager.save_settings(settings)
            assert Path(settings_manager.load_settings()["db_path"]).resolve().parent == tmp
            database.init_db()
            season = database.create_season(1, "Audit")
            database.import_ocr_scores(season, 1, [("AuditAlice", 100), ("AuditBob", 200)])
            database.import_ocr_scores(season, 2, [("AuditAlice", 150), ("AuditBob", 270)])

            if args.google:
                client = sheets_sync.authenticate(original["google_credentials_path"])
                book = client.open_by_key(original["google_sheets_id"])
                ws = book.add_worksheet(title="__GF2_AUDIT_" + uuid4().hex[:10], rows=10, cols=9)
                try:
                    ws.update(range_name="A1:I4", values=[
                        ["Date", "Day1", "Day2", "Day3", "Day4", "Day5", "Day6", "Day7", "Total"],
                        ["Total"], ["AuditAlice", 0, 0, 0, 0, 0, 0, 0, "=SUM(B3:H3)"],
                        ["AuditBob", 0, 0, 0, 0, 0, 0, 0, "=SUM(B4:H4)"],
                    ], value_input_option="USER_ENTERED")
                    preview = sheets_sync.prepare_sync(original["google_credentials_path"], book.id, season, worksheet_id=ws.id)
                    assert sheets_sync.execute_sync(preview).updated == 2
                    assert ws.get("B3:C4") == [["100", "50"], ["200", "70"]]
                    assert ws.get("I3:I4", value_render_option="FORMULA") == [["=SUM(B3:H3)"], ["=SUM(B4:H4)"]]
                    target = database.create_season(2, "Roundtrip")
                    reverse = sheets_sync.prepare_reverse_sync(original["google_credentials_path"], book.id, target, worksheet_id=ws.id)
                    assert sheets_sync.execute_reverse_sync(reverse).imported == 2
                    assert sorted(r["total_score"] for r in database.get_scores_for_season(target)) == [150, 270]
                    assert sheets_sync.prepare_sync(original["google_credentials_path"], book.id, target, worksheet_id=ws.id).changed_count == 0
                    report["google"] = "real export/import roundtrip; formulas preserved; repeat export unchanged"
                finally:
                    book.del_worksheet(ws)
                    report["google_temporary_sheet_deleted"] = True

            decoded = failed = 0
            latest = None
            for capture in (ROOT / "captures").rglob("*.pcap*"):
                try:
                    snapshot = load_guild_snapshot(capture)
                except CaptureDecodeError:
                    failed += 1
                    continue
                imported_season = database.create_season(100 + decoded, "Capture audit")
                database.import_network_scores(snapshot, imported_season, 1, {p.uid: None for p in snapshot.players if p.name not in {r['name'] for r in database.get_all_player_names_with_lock()}})
                assert len(database.get_scores_for_season(imported_season)) == len(snapshot.players)
                latest = snapshot
                decoded += 1
            report["captures"] = {"imported": decoded, "rejected_incomplete": failed}
            stop = threading.Event()
            timer = threading.Timer(3, stop.set)
            timer.start()
            try:
                with patch.object(live_capture, "ROOT", tmp):
                    try:
                        live_capture.capture_guild_snapshot(stop, on_ready=lambda: report.update(npcap="opened successfully"), timeout=4)
                    except live_capture.CaptureCancelled:
                        pass
                    except Exception as exc:
                        report["npcap_error"] = str(exc)
            finally:
                timer.cancel()

            from PIL import Image
            from PyQt6.QtWidgets import QApplication
            from ui.manual_mode_dialog import ManualModeDialog
            app = QApplication.instance() or QApplication([])
            dialog = ManualModeDialog(season, "Audit")
            paths = []
            for fmt in ["PNG", "JPEG", "WEBP", "BMP"]:
                path = tmp / ("sample." + fmt.lower())
                Image.new("RGB", (300, 80), "white").save(path, format=fmt)
                paths.append(str(path))
            dialog._add_files(paths)
            assert len(dialog._images) == 4
            bad = tmp / "bad.png"
            bad.write_bytes(b"not an image")
            dialog._add_files([str(bad)])
            assert len(dialog._images) == 4
            report["image_import"] = "PNG/JPEG/WebP/BMP accepted; corrupt PNG rejected"
            dialog.close()

            if args.render:
                from ui.main_window import MainWindow
                from ui.settings_dialog import SettingsDialog
                from ui.season_dialog import SeasonDialog
                from ui.badge_calibration import BadgeOffsetCalibrationDialog
                from ui.history_dialog import HistoryDialog
                from ui.sheets_tab_dialog import SheetsTabDialog
                from ui.sheets_preview_dialog import SheetsPreviewDialog
                from ui.sheets_import_preview_dialog import SheetsImportPreviewDialog
                from ui.network_import_dialog import NetworkImportDialog
                from core.sheets_sync import PlayerDiff, PlayerImportDiff, SyncPreview, ReversePreview
                export = SyncPreview([PlayerDiff("AuditAlice", "AuditAlice", 3, {d: "0" for d in range(1, 8)}, {d: 100 for d in range(1, 8)}, True)], [], [], [], "", "", 0)
                reverse = ReversePreview([PlayerImportDiff("AuditAlice", "AuditAlice", False, {1: 100}, {d: 100 for d in range(1, 8)}, {d: 0 for d in range(1, 8)}, True)], "", "", 0, season)
                report["rendered"] = []
                for scale, contrast in [(1.0, False), (1.6, True)]:
                    settings.update(font_scale=scale, high_contrast=contrast)
                    settings_manager.save_settings(settings)
                    font = app.font(); font.setPointSizeF(10 * scale); app.setFont(font)
                    factories = [MainWindow, SettingsDialog, SeasonDialog, BadgeOffsetCalibrationDialog,
                                 lambda: ManualModeDialog(season, "Audit"), lambda: HistoryDialog(season),
                                 lambda: SheetsTabDialog([(1, "Audit")], 1, "Audit"),
                                 lambda: SheetsPreviewDialog(export), lambda: SheetsImportPreviewDialog(reverse)]
                    if latest:
                        factories.append(lambda: NetworkImportDialog(latest, season, "Audit"))
                    for factory in factories:
                        window = factory()
                        window.show(); app.processEvents()
                        filename = f"{type(window).__name__}_{scale}.png"
                        window.grab().save(str(output / filename))
                        report["rendered"].append(filename)
                        window.close(); window.deleteLater(); app.processEvents()
            with closing(sqlite3.connect(settings["db_path"])) as conn:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    (output / ("integration_google.json" if args.google else "integration_local.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
