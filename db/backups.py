"""SQLite snapshots for bulk imports and guarded undo of the latest import."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from config.settings_manager import load_settings

KEEP_BACKUPS = 10


def _paths(db_path: str | Path | None = None) -> tuple[Path, Path]:
    database = Path(db_path or load_settings()["db_path"]).resolve()
    folder = database.parent / "backups"
    # Historical backups belong to guild_tracker.db. Other databases must never
    # share its undo index, even when they live in the same directory.
    if database.name != "guild_tracker.db":
        folder /= database.name
    folder.mkdir(parents=True, exist_ok=True)
    return database, folder


def _read_index(folder: Path) -> list[dict]:
    path = folder / "index.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_index(folder: Path, entries: list[dict]) -> None:
    path = folder / "index.json"
    temp = folder / "index.json.tmp"
    temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def create_backup(source: str, season_id: int, db_path: str | Path | None = None) -> Path:
    """Snapshot the database before a bulk write."""
    database, folder = _paths(db_path)
    if not database.exists():
        raise FileNotFoundError(database)
    name = f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid4().hex[:8]}_{source}.db"
    target = folder / name
    source_db = sqlite3.connect(database)
    backup_db = sqlite3.connect(target)
    try:
        source_db.backup(backup_db)
    finally:
        backup_db.close()
        source_db.close()
    return target


def register_import(
    backup_path: Path, source: str, season_id: int, db_path: str | Path | None = None
) -> None:
    """Record the audit boundary after a successful import."""
    database, folder = _paths(db_path)
    with closing(sqlite3.connect(database)) as conn:
        audit_end = conn.execute("SELECT COALESCE(MAX(id),0) FROM change_history").fetchone()[0]
    entries = _read_index(folder)
    entries.append({
        "file": backup_path.name, "source": source, "season_id": season_id,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "audit_end": audit_end, "undone": False,
    })
    entries.sort(key=lambda entry: entry["finished_at"], reverse=True)
    for stale in entries[KEEP_BACKUPS:]:
        (folder / stale["file"]).unlink(missing_ok=True)
    _write_index(folder, entries[:KEEP_BACKUPS])


def latest_import(db_path: str | Path | None = None) -> dict | None:
    _, folder = _paths(db_path)
    entries = _read_index(folder)
    return entries[0] if entries and not entries[0].get("undone") else None


def restore_latest_import(db_path: str | Path | None = None) -> dict:
    """Undo the latest import only if no subsequent edits exist."""
    database, folder = _paths(db_path)
    entry = latest_import(database)
    if entry is None:
        raise ValueError("Нет импорта, который можно отменить.")
    backup_path = folder / entry["file"]
    if not backup_path.exists():
        raise FileNotFoundError(backup_path)
    with closing(sqlite3.connect(database)) as conn:
        current_end = conn.execute("SELECT COALESCE(MAX(id),0) FROM change_history").fetchone()[0]
    if current_end != entry["audit_end"]:
        raise ValueError(
            "После импорта были другие изменения. Восстановите нужную копию вручную."
        )
    safety_copy = create_backup("before_undo", entry["season_id"], database)
    source_db = sqlite3.connect(backup_path)
    destination = sqlite3.connect(database)
    try:
        source_db.backup(destination)
    finally:
        destination.close()
        source_db.close()
    entries = _read_index(folder)
    entries[0]["undone"] = True
    entries[0]["safety_copy"] = safety_copy.name
    entries.insert(0, {
        "file": safety_copy.name, "source": "before_undo",
        "season_id": entry["season_id"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "audit_end": entry["audit_end"], "undone": True,
    })
    for stale in entries[KEEP_BACKUPS:]:
        (folder / stale["file"]).unlink(missing_ok=True)
    _write_index(folder, entries[:KEEP_BACKUPS])
    return entry
