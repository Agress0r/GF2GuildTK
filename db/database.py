import sqlite3
from pathlib import Path
from datetime import datetime
from config.settings_manager import load_settings


def get_connection() -> sqlite3.Connection:
    settings = load_settings()
    db_path = settings["db_path"]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number INTEGER NOT NULL,
            name TEXT,
            start_date TEXT,
            end_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            total_score INTEGER NOT NULL,
            position INTEGER,
            recorded_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (season_id) REFERENCES seasons(id),
            FOREIGN KEY (player_id) REFERENCES players(id),
            UNIQUE(season_id, player_id)
        );

        CREATE TABLE IF NOT EXISTS day_scores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id   INTEGER NOT NULL,
            player_id   INTEGER NOT NULL,
            day_number  INTEGER NOT NULL CHECK(day_number BETWEEN 1 AND 7),
            total_score INTEGER NOT NULL,
            recorded_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (season_id) REFERENCES seasons(id),
            FOREIGN KEY (player_id) REFERENCES players(id),
            UNIQUE(season_id, player_id, day_number)
        );
    """)
    conn.commit()
    # Add locked column if it doesn't exist yet (migration for existing DBs)
    try:
        c.execute("ALTER TABLE players ADD COLUMN locked INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.close()


# --- Seasons ---

def create_season(number: int, name: str = "", start_date: str = "", end_date: str = "", notes: str = "") -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO seasons (number, name, start_date, end_date, notes) VALUES (?,?,?,?,?)",
        (number, name, start_date, end_date, notes)
    )
    conn.commit()
    season_id = c.lastrowid
    conn.close()
    return season_id


def get_all_seasons() -> list[dict]:
    conn = get_connection()
    c = conn.cursor()
    rows = c.execute("SELECT * FROM seasons ORDER BY number DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_last_active_season() -> dict | None:
    """Return the season with the most recent score activity, or the newest season."""
    conn = get_connection()
    c = conn.cursor()
    row = c.execute("""
        SELECT s.id, s.number, s.name
        FROM seasons s
        LEFT JOIN scores sc ON sc.season_id = s.id
        LEFT JOIN day_scores ds ON ds.season_id = s.id
        GROUP BY s.id
        ORDER BY MAX(COALESCE(sc.recorded_at, ''), COALESCE(ds.recorded_at, '')) DESC,
                 s.created_at DESC
        LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row) if row else None


def get_season(season_id: int) -> dict | None:
    conn = get_connection()
    c = conn.cursor()
    row = c.execute("SELECT * FROM seasons WHERE id=?", (season_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_season(season_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM day_scores WHERE season_id=?", (season_id,))
    c.execute("DELETE FROM scores WHERE season_id=?", (season_id,))
    c.execute("DELETE FROM seasons WHERE id=?", (season_id,))
    conn.commit()
    conn.close()


# --- Players & Scores ---

def get_all_player_names_with_lock() -> list[dict]:
    """Return all players with id, name, locked flag. Used by fuzzy matcher."""
    conn = get_connection()
    rows = conn.execute("SELECT id, name, locked FROM players").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _fuzzy_resolve_name(ocr_name: str, existing: list[dict]) -> str:
    """
    Given an OCR'd name and list of existing players, return the canonical name.
    Locked players are matched with relaxed thresholds and take priority over
    unlocked ones. Falls back to returning ocr_name unchanged if thefuzz is
    not installed.
    """
    if not ocr_name or len(ocr_name) < 3:
        return ocr_name

    # Exact match — no fuzzy needed
    for p in existing:
        if p["name"] == ocr_name:
            return ocr_name

    try:
        from thefuzz import fuzz
        import Levenshtein
    except ImportError:
        return ocr_name

    ocr_lower = ocr_name.lower()

    # Thresholds differ by name length: short names tolerate lower ratio
    def _thresholds(name: str):
        if len(name) <= 4:
            return 55, 2   # ratio_min, lev_max
        if len(name) <= 7:
            return 62, 3
        return 70, 4

    best_locked_name   = None
    best_locked_ratio  = 0
    best_any_name      = ocr_name
    best_any_ratio     = 0

    for p in existing:
        cand_lower = p["name"].lower()
        ratio = fuzz.ratio(ocr_lower, cand_lower)
        dist  = Levenshtein.distance(ocr_lower, cand_lower)
        ratio_min, lev_max = _thresholds(p["name"])

        if ratio >= ratio_min and dist <= lev_max:
            if p.get("locked") and ratio > best_locked_ratio:
                best_locked_ratio = ratio
                best_locked_name  = p["name"]
            elif ratio > best_any_ratio:
                best_any_ratio = ratio
                best_any_name  = p["name"]

    # Locked match wins even if an unlocked match has higher ratio
    if best_locked_name is not None:
        return best_locked_name
    return best_any_name


def upsert_player(name: str, apply_fuzzy: bool = True, existing: list[dict] | None = None) -> int:
    """
    Insert or find a player by name. Optionally applies fuzzy name matching.
    Pass pre-fetched `existing` list for batch operations to avoid N+1 queries.
    """
    if apply_fuzzy:
        if existing is None:
            existing = get_all_player_names_with_lock()
        name = _fuzzy_resolve_name(name, existing)

    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
    conn.commit()
    row = c.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()
    conn.close()
    return row["id"]


def save_score(season_id: int, player_name: str, total_score: int, position: int,
               day_number: int | None = None, existing_players: list[dict] | None = None):
    """Save a player's score. If day_number is given, also upserts into day_scores."""
    player_id = upsert_player(player_name, apply_fuzzy=True, existing=existing_players)
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO scores (season_id, player_id, total_score, position)
           VALUES (?,?,?,?)
           ON CONFLICT(season_id, player_id) DO UPDATE SET
               total_score=excluded.total_score,
               position=excluded.position,
               recorded_at=datetime('now')
        """,
        (season_id, player_id, total_score, position)
    )
    if day_number is not None:
        c.execute(
            """INSERT INTO day_scores (season_id, player_id, day_number, total_score)
               VALUES (?,?,?,?)
               ON CONFLICT(season_id, player_id, day_number) DO UPDATE SET
                   total_score=excluded.total_score,
                   recorded_at=datetime('now')
            """,
            (season_id, player_id, day_number, total_score)
        )
    conn.commit()
    conn.close()


def save_day_score(season_id: int, player_id: int, day_number: int, total_score: int):
    """Directly upsert a day snapshot. Used for in-app editing."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """INSERT INTO day_scores (season_id, player_id, day_number, total_score)
           VALUES (?,?,?,?)
           ON CONFLICT(season_id, player_id, day_number) DO UPDATE SET
               total_score=excluded.total_score,
               recorded_at=datetime('now')
        """,
        (season_id, player_id, day_number, total_score)
    )
    # Update main scores table with the MAX snapshot across all days,
    # so editing an earlier day never overwrites a later day's value.
    row = c.execute(
        "SELECT MAX(total_score) AS max_snap FROM day_scores WHERE season_id=? AND player_id=?",
        (season_id, player_id)
    ).fetchone()
    best_snap = row["max_snap"] if row and row["max_snap"] is not None else total_score
    c.execute(
        """UPDATE scores SET total_score=?, recorded_at=datetime('now')
           WHERE season_id=? AND player_id=?
        """,
        (best_snap, season_id, player_id)
    )
    conn.commit()
    conn.close()


def compute_daily_scores(snapshots: dict) -> dict:
    """
    Given {day_number: total_score} (only days with data),
    return {1..7: daily_delta or None}.

    daily_delta = snapshot[N] - snapshot[N-1] (clamped to 0 if negative).
    If a day has no snapshot, returns None and carries forward last known total.
    """
    result = {}
    last_known = None
    for day in range(1, 8):
        if day in snapshots:
            current = snapshots[day]
            if last_known is None:
                daily = current
            else:
                daily = max(0, current - last_known)
            result[day] = daily
            last_known = current
        else:
            result[day] = None
            # last_known unchanged — next delta still diffs against it
    return result


def get_scores_for_season(season_id: int) -> list[dict]:
    """
    Returns one dict per player with position, name, player_id, locked,
    total_score, day_snapshots {1..7}, and daily_scores {1..7}.
    """
    conn = get_connection()
    c = conn.cursor()
    base_rows = c.execute("""
        SELECT s.position, p.name, p.id AS player_id, p.locked, s.total_score
        FROM scores s
        JOIN players p ON p.id = s.player_id
        WHERE s.season_id = ?
        ORDER BY s.position ASC
    """, (season_id,)).fetchall()

    day_rows = c.execute(
        "SELECT player_id, day_number, total_score FROM day_scores WHERE season_id=?",
        (season_id,)
    ).fetchall()
    conn.close()

    # Build per-player snapshot dict
    snapshots: dict[int, dict[int, int]] = {}
    for dr in day_rows:
        pid = dr["player_id"]
        if pid not in snapshots:
            snapshots[pid] = {}
        snapshots[pid][dr["day_number"]] = dr["total_score"]

    result = []
    for row in base_rows:
        pid = row["player_id"]
        snap = snapshots.get(pid, {})
        daily = compute_daily_scores(snap)
        result.append({
            "position": row["position"],
            "name": row["name"],
            "player_id": pid,
            "locked": bool(row["locked"]),
            "total_score": row["total_score"],
            "day_snapshots": {d: snap.get(d) for d in range(1, 8)},
            "daily_scores": daily,
        })
    return result


def get_player_history(player_name: str) -> list[dict]:
    conn = get_connection()
    c = conn.cursor()
    rows = c.execute("""
        SELECT se.number as season_number, se.name as season_name,
               sc.total_score, sc.position
        FROM scores sc
        JOIN players p ON p.id = sc.player_id
        JOIN seasons se ON se.id = sc.season_id
        WHERE p.name = ?
        ORDER BY se.number DESC
    """, (player_name,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def rename_player(player_id: int, new_name: str) -> bool:
    """
    Rename a player. Returns False if new_name already belongs to another player.
    """
    new_name = new_name.strip()
    if not new_name:
        return False
    conn = get_connection()
    c = conn.cursor()
    existing = c.execute(
        "SELECT id FROM players WHERE name=? AND id != ?", (new_name, player_id)
    ).fetchone()
    if existing:
        conn.close()
        return False
    c.execute("UPDATE players SET name=? WHERE id=?", (new_name, player_id))
    conn.commit()
    conn.close()
    return True


def set_player_locked(player_id: int, locked: bool):
    """Set the locked flag for a player."""
    conn = get_connection()
    conn.execute("UPDATE players SET locked=? WHERE id=?", (int(locked), player_id))
    conn.commit()
    conn.close()


def get_player_lock_state(player_id: int) -> bool:
    """Return True if the player is locked."""
    conn = get_connection()
    row = conn.execute("SELECT locked FROM players WHERE id=?", (player_id,)).fetchone()
    conn.close()
    return bool(row["locked"]) if row else False


def remove_player_from_season(season_id: int, player_id: int):
    """Remove a player's data from the current season (scores + day_scores)."""
    conn = get_connection()
    conn.execute("DELETE FROM day_scores WHERE season_id=? AND player_id=?", (season_id, player_id))
    conn.execute("DELETE FROM scores WHERE season_id=? AND player_id=?", (season_id, player_id))
    conn.commit()
    conn.close()


def add_player_to_season(season_id: int, name: str) -> tuple[bool, str]:
    """
    Manually add a player to a season (without OCR).
    Returns (True, "") on success, or (False, reason) on error.
    """
    name = name.strip()
    if not name:
        return False, "Имя не может быть пустым."

    conn = get_connection()
    existing = conn.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()
    if existing:
        player_id = existing["id"]
        in_season = conn.execute(
            "SELECT 1 FROM scores WHERE season_id=? AND player_id=?",
            (season_id, player_id)
        ).fetchone()
        if in_season:
            conn.close()
            return False, f"Игрок «{name}» уже есть в этом сезоне."
    else:
        conn.execute("INSERT INTO players (name) VALUES (?)", (name,))
        conn.commit()
        player_id = conn.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()["id"]

    conn.execute(
        "INSERT OR IGNORE INTO scores (season_id, player_id, total_score, position) VALUES (?,?,0,NULL)",
        (season_id, player_id)
    )
    conn.commit()
    conn.close()
    return True, ""
