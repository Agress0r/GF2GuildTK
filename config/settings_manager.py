import json
import sys
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "settings.json" if getattr(sys, "frozen", False) else APP_DIR / "config" / "settings.json"

DEFAULT_SETTINGS = {
    "db_path": str(APP_DIR / "guild_tracker.db"),
    "google_sheets_id": "",
    "google_credentials_path": "",
    "google_sheets_history": [],  # list of {"id": str, "name": str, "last_used": str}
    "google_worksheet_ids": {},  # spreadsheet ID -> last selected worksheet ID
    "font_scale": 1.0,
    "high_contrast": False,
}


def _sanitize_paths(settings: dict) -> dict:
    """Сбрасывает отсутствующий ключ Google; путь новой БД сохраняется.

    Несуществующая БД создаётся по выбранному пути. Подмена этого пути
    основной базой опасна: импорт попадёт в другой файл.
    """
    path_fields = {
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


def _profile_key(width: int, height: int, scale: int) -> str:
    return f"{width}x{height}@{scale}"


def get_badge_offsets(width: int | None = None, height: int | None = None,
                      scale: int = 100) -> dict | None:
    """Get offsets for image resolution and game UI scale, with legacy fallback."""
    settings = load_settings()
    if width is not None and height is not None:
        key = _profile_key(width, height, scale)
        profiles = settings.get("badge_profiles", {})
        if profiles:
            return profiles.get(key)
    return settings.get("badge_offsets")


def save_badge_offsets(offsets: dict, width: int | None = None,
                       height: int | None = None, scale: int = 100):
    """Persist calibration for a resolution and UI scale."""
    settings = load_settings()
    if width is not None and height is not None:
        settings.setdefault("badge_profiles", {})[_profile_key(width, height, scale)] = offsets
    else:
        settings["badge_offsets"] = offsets
    save_settings(settings)


def list_badge_profiles() -> list[str]:
    return sorted(load_settings().get("badge_profiles", {}))
