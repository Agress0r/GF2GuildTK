import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"

DEFAULT_SETTINGS = {
    "scroll_pause": 1.2,
    "scroll_amount": 3,
    "ticks_per_row": 5,
    "countdown_seconds": 5,
    "db_path": str(Path(__file__).parent.parent / "guild_tracker.db"),
    "google_sheets_id": "",
    "google_credentials_path": "",
    "google_sheets_history": [],  # list of {"id": str, "name": str, "last_used": str}
    "roi_profiles": {}
}


def _sanitize_paths(settings: dict) -> dict:
    """Сбрасывает абсолютные пути к несуществующим файлам на дефолтные.
    Предотвращает поломку при копировании settings.json с другой машины."""
    path_fields = {
        "db_path": DEFAULT_SETTINGS["db_path"],
        "google_credentials_path": DEFAULT_SETTINGS["google_credentials_path"],
    }
    for field, default in path_fields.items():
        val = settings.get(field, "")
        if val:
            p = Path(val)
            if p.is_absolute() and not p.exists():
                settings[field] = default
    return settings


def load_settings() -> dict:
    if not CONFIG_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Merge with defaults for any missing keys
    merged = DEFAULT_SETTINGS.copy()
    merged.update(data)
    merged = _sanitize_paths(merged)
    return merged


def save_settings(settings: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def get_roi_for_resolution(width: int, height: int) -> dict | None:
    settings = load_settings()
    key = f"{width}x{height}"
    return settings.get("roi_profiles", {}).get(key)


def save_roi_for_resolution(width: int, height: int, roi: dict):
    settings = load_settings()
    key = f"{width}x{height}"
    if "roi_profiles" not in settings:
        settings["roi_profiles"] = {}
    settings["roi_profiles"][key] = roi
    save_settings(settings)


def get_badge_offsets() -> dict | None:
    """Return saved badge offset calibration or None if not calibrated."""
    return load_settings().get("badge_offsets")


def save_badge_offsets(offsets: dict):
    """Save badge offset calibration (name/score offsets relative to badge top-left)."""
    settings = load_settings()
    settings["badge_offsets"] = offsets
    save_settings(settings)
