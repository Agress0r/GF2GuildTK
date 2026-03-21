"""
Google Sheets sync module.

Writes daily_scores (per-day deltas) from the local SQLite DB into a fixed-structure
Google Sheet:
  Row 1:   headers (Дата | day dates | Сезон | …)
  Row 2:   "Всего очков" totals
  Rows 3+: players  →  Col A = name, Cols B–H = days 1–7, Col I = season total (formula)

day_number mapping is FIXED: day 1 → col B (index 2), day 7 → col H (index 8).
The season total column (I) is never touched.

Two-phase API:
  prepare_sync()  — reads the sheet, builds diffs, returns SyncPreview (no writes)
  execute_sync()  — writes the pre-built cells to the sheet
  sync()          — convenience wrapper: prepare + execute in one call
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import gspread
from google.oauth2.service_account import Credentials


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]
NAME_COL = 1          # Col A (gspread 1-based)
DAY_COL_START = 2     # Col B = day_number 1
PLAYER_START_ROW = 3  # rows 1 = headers, 2 = totals, 3+ = players
TOTAL_ROWS_HEADER = "Всего очков"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class PlayerDiff:
    sheet_name: str           # name as it appears in Col A
    db_name: str | None       # matched DB player name (None = unmatched)
    row_idx: int              # 1-based row index in the worksheet
    current: dict[int, str]   # {day_num: current cell value string}
    proposed: dict[int, int]  # {day_num: new value from DB}
    has_changes: bool         # True if at least one day value differs


@dataclass
class SyncPreview:
    diffs: list[PlayerDiff]       # matched players (include unchanged ones)
    unmatched_sheet: list[str]    # in sheet Col A, no DB match found
    unmatched_db: list[str]       # in DB, no sheet row found
    cells: list[Any]              # list[gspread.Cell] ready to write
    creds_path: str               # stored for execute_sync re-auth
    sheet_id: str
    worksheet_index: int

    @property
    def changed_count(self) -> int:
        return sum(1 for d in self.diffs if d.has_changes)

    @property
    def matched_count(self) -> int:
        return len(self.diffs)


@dataclass
class SyncResult:
    updated: int = 0
    unmatched_sheet: list[str] = field(default_factory=list)
    unmatched_db: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Обновлено игроков: {self.updated}"]
        if self.unmatched_sheet:
            lines.append("Не найдено в БД (строки таблицы):\n  " + ", ".join(self.unmatched_sheet))
        if self.unmatched_db:
            lines.append("Нет в таблице (есть в БД):\n  " + ", ".join(self.unmatched_db))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def authenticate(creds_path: str) -> gspread.Client:
    """Return an authorised gspread Client using a Service Account JSON key."""
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)


def get_spreadsheet_name(creds_path: str, sheet_id: str) -> str:
    """Return the Google Sheets document title (used for the history UI)."""
    client = authenticate(creds_path)
    return client.open_by_key(sheet_id).title


# ---------------------------------------------------------------------------
# Fuzzy matching (mirrors logic from db/database.py)
# ---------------------------------------------------------------------------
def _thresholds(name: str) -> tuple[int, int]:
    """Return (ratio_min, lev_max) based on name length."""
    if len(name) <= 4:
        return 55, 2
    if len(name) <= 7:
        return 62, 3
    return 70, 4


def _find_best_match(sheet_name: str, db_players: list[dict]) -> dict | None:
    """
    Find the best matching DB player for a sheet row name.
    Returns the player dict or None if no match meets the thresholds.
    """
    # Exact match first
    for p in db_players:
        if p["name"] == sheet_name:
            return p

    try:
        from thefuzz import fuzz
        import Levenshtein
    except ImportError:
        return None

    sheet_lower = sheet_name.lower()
    best: dict | None = None
    best_ratio = 0

    for p in db_players:
        cand_lower = p["name"].lower()
        ratio = fuzz.ratio(sheet_lower, cand_lower)
        dist = Levenshtein.distance(sheet_lower, cand_lower)
        ratio_min, lev_max = _thresholds(p["name"])
        if ratio >= ratio_min and dist <= lev_max and ratio > best_ratio:
            best_ratio = ratio
            best = p

    return best


# ---------------------------------------------------------------------------
# Phase 1: Read sheet + build diff (no writes)
# ---------------------------------------------------------------------------
def prepare_sync(
    creds_path: str,
    sheet_id: str,
    season_id: int,
    worksheet_index: int = 0,
) -> SyncPreview:
    """
    Read the sheet and local DB, build a SyncPreview without writing anything.
    The returned SyncPreview contains ready-to-write cells for execute_sync().
    """
    from db.database import get_scores_for_season

    client = authenticate(creds_path)
    ws = client.open_by_key(sheet_id).get_worksheet(worksheet_index)
    all_values = ws.get_all_values()  # one API call

    # Build name → row_index map (rows PLAYER_START_ROW+ in Col A)
    sheet_player_rows: dict[str, int] = {}
    for row_idx, row in enumerate(all_values, start=1):
        if row_idx < PLAYER_START_ROW:
            continue
        cell_name = row[NAME_COL - 1].strip() if row else ""
        if cell_name and cell_name != TOTAL_ROWS_HEADER:
            sheet_player_rows[cell_name] = row_idx

    db_data = get_scores_for_season(season_id)

    diffs: list[PlayerDiff] = []
    cells: list[gspread.Cell] = []
    matched_db_names: set[str] = set()
    unmatched_sheet: list[str] = []

    for sheet_name, row_idx in sheet_player_rows.items():
        match = _find_best_match(sheet_name, db_data)
        if match:
            # Read current sheet values for this player's day columns
            sheet_row = all_values[row_idx - 1]  # 0-based
            current: dict[int, str] = {}
            for day_num in range(1, 8):
                col_idx = DAY_COL_START - 1 + (day_num - 1)  # 0-based
                val = sheet_row[col_idx] if col_idx < len(sheet_row) else ""
                current[day_num] = val.strip()

            proposed: dict[int, int] = {}
            row_cells: list[gspread.Cell] = []
            for day_num in range(1, 8):
                score = match["daily_scores"].get(day_num)
                value = score if score is not None else 0
                proposed[day_num] = value
                row_cells.append(gspread.Cell(row_idx, DAY_COL_START + (day_num - 1), value))

            # Detect changes: compare proposed int to current string
            has_changes = any(
                str(proposed[d]) != current.get(d, "") for d in range(1, 8)
            )

            diffs.append(PlayerDiff(
                sheet_name=sheet_name,
                db_name=match["name"],
                row_idx=row_idx,
                current=current,
                proposed=proposed,
                has_changes=has_changes,
            ))
            cells.extend(row_cells)
            matched_db_names.add(match["name"])
        else:
            unmatched_sheet.append(sheet_name)

    unmatched_db = [p["name"] for p in db_data if p["name"] not in matched_db_names]

    return SyncPreview(
        diffs=diffs,
        unmatched_sheet=unmatched_sheet,
        unmatched_db=unmatched_db,
        cells=cells,
        creds_path=creds_path,
        sheet_id=sheet_id,
        worksheet_index=worksheet_index,
    )


# ---------------------------------------------------------------------------
# Phase 2: Write (re-authenticates in the worker thread)
# ---------------------------------------------------------------------------
def execute_sync(preview: SyncPreview) -> SyncResult:
    """Write the pre-built cells from a SyncPreview to the sheet."""
    if not preview.cells:
        return SyncResult(
            updated=preview.matched_count,
            unmatched_sheet=preview.unmatched_sheet,
            unmatched_db=preview.unmatched_db,
        )

    client = authenticate(preview.creds_path)
    ws = client.open_by_key(preview.sheet_id).get_worksheet(preview.worksheet_index)
    ws.update_cells(preview.cells, value_input_option="USER_ENTERED")

    return SyncResult(
        updated=preview.matched_count,
        unmatched_sheet=preview.unmatched_sheet,
        unmatched_db=preview.unmatched_db,
    )


# ---------------------------------------------------------------------------
# Convenience wrapper (backwards compatible)
# ---------------------------------------------------------------------------
def sync(
    creds_path: str,
    sheet_id: str,
    season_id: int,
    worksheet_index: int = 0,
) -> SyncResult:
    """Read sheet, build diffs, and immediately write. No preview."""
    preview = prepare_sync(creds_path, sheet_id, season_id, worksheet_index)
    return execute_sync(preview)


# ---------------------------------------------------------------------------
# Reverse sync: Google Sheets → SQLite DB
# ---------------------------------------------------------------------------

@dataclass
class PlayerImportDiff:
    sheet_name: str                       # имя из Col A таблицы
    db_name: str | None                   # совпавшее имя в БД (None = новый)
    is_new_player: bool
    sheet_deltas: dict[int, int]          # {day_num: delta} как есть в таблице
    sheet_snapshots: dict[int, int]       # {day_num: cumulative} после конвертации
    db_snapshots: dict[int, int | None]   # {day_num: текущий снапшот в БД}
    has_changes: bool


@dataclass
class ReversePreview:
    diffs: list[PlayerImportDiff]
    creds_path: str
    sheet_id: str
    worksheet_index: int
    season_id: int

    @property
    def changed_count(self) -> int:
        return sum(1 for d in self.diffs if d.has_changes)

    @property
    def new_players_count(self) -> int:
        return sum(1 for d in self.diffs if d.is_new_player)


@dataclass
class ReverseResult:
    imported: int = 0
    created_players: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Импортировано игроков: {self.imported}"]
        if self.created_players:
            lines.append("Создано новых игроков:\n  " + ", ".join(self.created_players))
        if self.skipped:
            lines.append("Пропущено (без изменений): " + str(len(self.skipped)))
        return "\n".join(lines)


def _deltas_to_snapshots(deltas: dict[int, int]) -> dict[int, int]:
    """
    Конвертирует дельты {day_num: delta} в накопительные снапшоты.
    Нулевые дни включаются: снапшот = предыдущий + 0.
    """
    result = {}
    running = 0
    for day in range(1, 8):
        running += deltas.get(day, 0)
        result[day] = running
    return result


def prepare_reverse_sync(
    creds_path: str,
    sheet_id: str,
    season_id: int,
    worksheet_index: int = 0,
) -> ReversePreview:
    """
    Читает таблицу и текущую БД, строит ReversePreview без записи в БД.
    Для каждого игрока таблицы конвертирует дельты → снапшоты
    и сравнивает с текущим состоянием БД.
    """
    from db.database import get_scores_for_season

    client = authenticate(creds_path)
    ws = client.open_by_key(sheet_id).get_worksheet(worksheet_index)
    all_values = ws.get_all_values()

    # Читаем игроков из таблицы (строки PLAYER_START_ROW+)
    sheet_players: list[tuple[str, dict[int, int]]] = []
    for row_idx, row in enumerate(all_values, start=1):
        if row_idx < PLAYER_START_ROW:
            continue
        cell_name = row[NAME_COL - 1].strip() if row else ""
        if not cell_name or cell_name == TOTAL_ROWS_HEADER:
            continue
        deltas: dict[int, int] = {}
        for day_num in range(1, 8):
            col_idx = DAY_COL_START - 1 + (day_num - 1)
            raw = row[col_idx].strip() if col_idx < len(row) else ""
            try:
                deltas[day_num] = int(raw) if raw else 0
            except ValueError:
                deltas[day_num] = 0
        sheet_players.append((cell_name, deltas))

    db_data = get_scores_for_season(season_id)

    diffs: list[PlayerImportDiff] = []

    for sheet_name, deltas in sheet_players:
        match = _find_best_match(sheet_name, db_data)
        snapshots = _deltas_to_snapshots(deltas)

        if match:
            db_snaps = {d: match["day_snapshots"].get(d) for d in range(1, 8)}
            has_changes = any(
                snapshots.get(d) != db_snaps.get(d)
                for d in range(1, 8)
            )
            diffs.append(PlayerImportDiff(
                sheet_name=sheet_name,
                db_name=match["name"],
                is_new_player=False,
                sheet_deltas=deltas,
                sheet_snapshots=snapshots,
                db_snapshots=db_snaps,
                has_changes=has_changes,
            ))
        else:
            db_snaps_empty = {d: None for d in range(1, 8)}
            has_changes = any(v > 0 for v in snapshots.values())
            diffs.append(PlayerImportDiff(
                sheet_name=sheet_name,
                db_name=None,
                is_new_player=True,
                sheet_deltas=deltas,
                sheet_snapshots=snapshots,
                db_snapshots=db_snaps_empty,
                has_changes=has_changes,
            ))

    return ReversePreview(
        diffs=diffs,
        creds_path=creds_path,
        sheet_id=sheet_id,
        worksheet_index=worksheet_index,
        season_id=season_id,
    )


def execute_reverse_sync(preview: ReversePreview) -> ReverseResult:
    """
    Пишет снапшоты из ReversePreview в SQLite БД.
    Новые игроки создаются автоматически.
    """
    from db.database import get_connection

    result = ReverseResult()

    conn = get_connection()
    c = conn.cursor()

    for diff in preview.diffs:
        if not diff.has_changes:
            result.skipped.append(diff.sheet_name)
            continue

        name = diff.db_name if diff.db_name else diff.sheet_name

        # Inline upsert player — одно соединение, иначе SQLite "database is locked"
        c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
        row = c.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()
        player_id = row["id"]

        if diff.is_new_player:
            result.created_players.append(name)

        max_snapshot = 0
        for day_num in range(1, 8):
            snap = diff.sheet_snapshots.get(day_num, 0)
            c.execute(
                """INSERT INTO day_scores (season_id, player_id, day_number, total_score)
                   VALUES (?,?,?,?)
                   ON CONFLICT(season_id, player_id, day_number) DO UPDATE SET
                       total_score=excluded.total_score,
                       recorded_at=datetime('now')
                """,
                (preview.season_id, player_id, day_num, snap)
            )
            if snap > max_snapshot:
                max_snapshot = snap

        c.execute(
            """INSERT INTO scores (season_id, player_id, total_score, position)
               VALUES (?,?,?,NULL)
               ON CONFLICT(season_id, player_id) DO UPDATE SET
                   total_score=excluded.total_score,
                   recorded_at=datetime('now')
            """,
            (preview.season_id, player_id, max_snapshot)
        )
        result.imported += 1

    conn.commit()
    conn.close()
    return result
