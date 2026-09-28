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
import re

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
    choices: dict[int, str] = field(default_factory=dict)  # day -> 'db' or 'sheet'

    def selected_value(self, day: int) -> int | str:
        return self.current.get(day, "") if self.choices.get(day) == "sheet" else self.proposed[day]

    def selected_changes(self) -> bool:
        return any(str(self.selected_value(day)) != self.current.get(day, "")
                   for day in range(1, 8))


@dataclass
class SyncPreview:
    diffs: list[PlayerDiff]       # matched players (include unchanged ones)
    unmatched_sheet: list[str]    # in sheet Col A, no DB match found
    unmatched_db: list[str]       # in DB, no sheet row found
    cells: list[Any]              # list[gspread.Cell] ready to write
    creds_path: str               # stored for execute_sync re-auth
    sheet_id: str
    worksheet_index: int
    worksheet_id: int | None = None
    worksheet_title: str = ""

    @property
    def changed_count(self) -> int:
        return sum(1 for d in self.diffs if d.selected_changes())

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
    client = gspread.authorize(creds)
    client.http_client.set_timeout(30)
    return client


def get_spreadsheet_name(creds_path: str, sheet_id: str) -> str:
    """Return the Google Sheets document title (used for the history UI)."""
    client = authenticate(creds_path)
    return client.open_by_key(sheet_id).title


def list_import_worksheets(creds_path: str, sheet_id: str) -> list[tuple[int, str]]:
    """Return visible worksheet IDs and titles in tab order."""
    spreadsheet = authenticate(creds_path).open_by_key(sheet_id)
    return [(ws.id, ws.title) for ws in spreadsheet.worksheets(exclude_hidden=True)]


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
    *,
    worksheet_id: int | None = None,
) -> SyncPreview:
    """
    Read the sheet and local DB, build a SyncPreview without writing anything.
    The returned SyncPreview contains ready-to-write cells for execute_sync().
    """
    from db.database import get_scores_for_season

    client = authenticate(creds_path)
    spreadsheet = client.open_by_key(sheet_id)
    ws = (spreadsheet.get_worksheet_by_id(worksheet_id) if worksheet_id is not None
          else spreadsheet.get_worksheet(worksheet_index))
    if ws is None:
        raise ValueError("Выбранный лист Google Таблицы больше не существует.")
    all_values = ws.get_all_values()  # one API call

    # Build name → row_index map (rows PLAYER_START_ROW+ in Col A)
    sheet_player_rows: dict[str, int] = {}
    for row_idx, row in enumerate(all_values, start=1):
        if row_idx < PLAYER_START_ROW:
            continue
        cell_name = row[NAME_COL - 1].strip() if row else ""
        if cell_name and cell_name != TOTAL_ROWS_HEADER:
            if cell_name in sheet_player_rows:
                raise ValueError(f"В таблице повторяется игрок «{cell_name}».")
            sheet_player_rows[cell_name] = row_idx

    db_data = get_scores_for_season(season_id)

    diffs: list[PlayerDiff] = []
    cells: list[gspread.Cell] = []
    matched_db_names: set[str] = set()
    unmatched_sheet: list[str] = []

    for sheet_name, row_idx in sheet_player_rows.items():
        match = _find_best_match(sheet_name, db_data)
        if match:
            if match["name"] in matched_db_names:
                raise ValueError(f"Несколько строк соответствуют игроку «{match['name']}». Исправьте имена в таблице.")
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
        worksheet_id=getattr(ws, "id", worksheet_id),
        worksheet_title=getattr(ws, "title", ""),
    )


# ---------------------------------------------------------------------------
# Phase 2: Write (re-authenticates in the worker thread)
# ---------------------------------------------------------------------------
def execute_sync(preview: SyncPreview) -> SyncResult:
    """Write reviewed cells only, refusing to overwrite newer sheet edits."""
    if not preview.changed_count:
        return SyncResult(
            updated=0,
            unmatched_sheet=preview.unmatched_sheet,
            unmatched_db=preview.unmatched_db,
        )

    client = authenticate(preview.creds_path)
    spreadsheet = client.open_by_key(preview.sheet_id)
    ws = (spreadsheet.get_worksheet_by_id(preview.worksheet_id)
          if preview.worksheet_id is not None else spreadsheet.get_worksheet(preview.worksheet_index))
    if ws is None:
        raise ValueError("Выбранный лист Google Таблицы больше не существует.")
    current_rows = ws.get_all_values()
    cells = []
    for diff in preview.diffs:
        row = current_rows[diff.row_idx - 1] if diff.row_idx <= len(current_rows) else []
        if diff.selected_changes() and (not row or row[0].strip() != diff.sheet_name):
            raise ValueError(f"Строка игрока «{diff.sheet_name}» изменилась после предпросмотра. Повторите экспорт.")
        for day in range(1, 8):
            selected = diff.selected_value(day)
            old = diff.current.get(day, "")
            if str(selected) == old:
                continue
            row = current_rows[diff.row_idx - 1] if diff.row_idx <= len(current_rows) else []
            col = DAY_COL_START - 1 + day - 1
            now = row[col].strip() if col < len(row) else ""
            if now != old:
                raise ValueError(
                    f"Ячейка игрока «{diff.sheet_name}», день {day} изменилась после предпросмотра. "
                    "Обновите данные и повторите экспорт."
                )
            cells.append(gspread.Cell(diff.row_idx, DAY_COL_START + day - 1, selected))
    if cells:
        ws.update_cells(cells, value_input_option="USER_ENTERED")

    return SyncResult(
        updated=preview.changed_count,
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
    sheet_present: dict[int, bool] = field(default_factory=dict)  # days eligible for import
    choices: dict[int, str] = field(default_factory=dict)  # day -> 'sheet' or 'db'

    def selected_snapshot(self, day: int) -> int | None:
        if self.sheet_present and not self.sheet_present.get(day, False):
            return self.db_snapshots.get(day)
        return self.db_snapshots.get(day) if self.choices.get(day) == "db" else self.sheet_snapshots.get(day, 0)

    def selected_changes(self) -> bool:
        if self.is_new_player:
            return any((self.selected_snapshot(day) or 0) > 0 for day in range(1, 8))
        return any(self.selected_snapshot(day) != self.db_snapshots.get(day) for day in range(1, 8))


@dataclass
class ReversePreview:
    diffs: list[PlayerImportDiff]
    creds_path: str
    sheet_id: str
    worksheet_index: int
    season_id: int
    worksheet_id: int | None = None
    worksheet_title: str = ""
    active_day: int | None = None

    @property
    def changed_count(self) -> int:
        return sum(1 for d in self.diffs if d.selected_changes())

    @property
    def new_players_count(self) -> int:
        return sum(1 for d in self.diffs if d.is_new_player and d.selected_changes())


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
    *,
    worksheet_id: int | None = None,
) -> ReversePreview:
    """
    Читает таблицу и текущую БД, строит ReversePreview без записи в БД.
    Для каждого игрока таблицы конвертирует дельты → снапшоты
    и сравнивает с текущим состоянием БД. Нули после последнего дня
    с ненулевыми очками считаются заготовкой шаблона и не импортируются.
    """
    from db.database import get_scores_for_season

    client = authenticate(creds_path)
    spreadsheet = client.open_by_key(sheet_id)
    ws = (spreadsheet.get_worksheet_by_id(worksheet_id) if worksheet_id is not None
          else spreadsheet.get_worksheet(worksheet_index))
    if ws is None:
        raise ValueError("Выбранный лист Google Таблицы больше не существует.")
    all_values = ws.get_all_values()

    # Читаем игроков из таблицы (строки PLAYER_START_ROW+)
    sheet_players: list[tuple[str, dict[int, int], dict[int, bool]]] = []
    seen_names = set()
    for row_idx, row in enumerate(all_values, start=1):
        if row_idx < PLAYER_START_ROW:
            continue
        cell_name = row[NAME_COL - 1].strip() if row else ""
        if not cell_name or cell_name == TOTAL_ROWS_HEADER:
            continue
        if cell_name in seen_names:
            raise ValueError(f"В таблице повторяется игрок «{cell_name}».")
        seen_names.add(cell_name)
        deltas: dict[int, int] = {}
        present: dict[int, bool] = {}
        for day_num in range(1, 8):
            col_idx = DAY_COL_START - 1 + (day_num - 1)
            raw = row[col_idx].strip() if col_idx < len(row) else ""
            present[day_num] = bool(raw)
            try:
                # Accept integer grouping, never silently turn 1,5 into 15.
                if raw and not re.fullmatch(r"[0-9]+|[0-9]{1,3}(?:[ ,\u00a0\u202f][0-9]{3})+", raw):
                    raise ValueError("not an integer")
                value = int(re.sub(r"[ ,\u00a0\u202f]", "", raw)) if raw else 0
                if value < 0:
                    raise ValueError("negative")
                deltas[day_num] = value
            except ValueError as exc:
                raise ValueError(
                    f"Некорректный счёт у игрока «{cell_name}», день {day_num}: {raw!r}"
                ) from exc
        sheet_players.append((cell_name, deltas, present))

    # The template fills future day cells with literal zeroes. They are not
    # observations: importing them would carry the last cumulative score into
    # every future day and create spurious changes for nearly every player.
    active_day = max(
        (day for _, deltas, _ in sheet_players
         for day, score in deltas.items() if score > 0),
        default=0,
    )

    db_data = get_scores_for_season(season_id)

    diffs: list[PlayerImportDiff] = []
    matched_names = set()
    # Players are shared across seasons. A new season has no scores yet, but
    # importing its roster must reuse existing player identities.
    from db.database import get_all_player_names_with_lock
    season_names = {player["name"] for player in db_data}
    db_data.extend({**player, "day_snapshots": {}}
                   for player in get_all_player_names_with_lock()
                   if player["name"] not in season_names)

    for sheet_name, deltas, present in sheet_players:
        match = _find_best_match(sheet_name, db_data)
        snapshots = _deltas_to_snapshots(deltas)
        importable = {day: present[day] and day <= active_day for day in range(1, 8)}

        if match:
            if match["name"] in matched_names:
                raise ValueError(f"Несколько строк соответствуют игроку «{match['name']}». Исправьте имена в таблице.")
            matched_names.add(match["name"])
            db_snaps = {d: match["day_snapshots"].get(d) for d in range(1, 8)}
            has_changes = any(
                snapshots[d] != db_snaps[d]
                for d in range(1, 8) if importable[d]
            )
            diffs.append(PlayerImportDiff(
                sheet_name=sheet_name,
                db_name=match["name"],
                is_new_player=False,
                sheet_deltas=deltas,
                sheet_snapshots=snapshots,
                db_snapshots=db_snaps,
                has_changes=has_changes,
                sheet_present=importable,
                choices={day: "db" for day in range(1, 8) if not importable[day]},
            ))
        else:
            db_snaps_empty = {d: None for d in range(1, 8)}
            has_changes = any(snapshots[day] > 0 for day in range(1, 8) if importable[day])
            diffs.append(PlayerImportDiff(
                sheet_name=sheet_name,
                db_name=None,
                is_new_player=True,
                sheet_deltas=deltas,
                sheet_snapshots=snapshots,
                db_snapshots=db_snaps_empty,
                has_changes=has_changes,
                sheet_present=importable,
                choices={day: "db" for day in range(1, 8) if not importable[day]},
            ))

    return ReversePreview(
        diffs=diffs,
        creds_path=creds_path,
        sheet_id=sheet_id,
        worksheet_index=worksheet_index,
        season_id=season_id,
        worksheet_id=worksheet_id,
        worksheet_title=ws.title if worksheet_id is not None else "",
        active_day=active_day,
    )


def execute_reverse_sync(preview: ReversePreview) -> ReverseResult:
    """
    Пишет снапшоты из ReversePreview в SQLite БД.
    Новые игроки создаются автоматически.
    """
    from db.database import get_connection, set_audit_source
    from db.backups import create_backup, register_import

    result = ReverseResult()
    if not preview.changed_count:
        return result
    conn = get_connection()
    backup_path = None
    try:
        database_path = conn.execute("PRAGMA database_list").fetchone()[2]
        backup_path = create_backup("google", preview.season_id, database_path)
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        set_audit_source(conn, "google")
        if c.execute("SELECT 1 FROM seasons WHERE id=?", (preview.season_id,)).fetchone() is None:
            raise ValueError("Сезон больше не существует.")
        for diff in preview.diffs:
            if not diff.selected_changes():
                result.skipped.append(diff.sheet_name)
                continue
            name = diff.db_name or diff.sheet_name
            existing = c.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()
            if diff.is_new_player and existing is not None:
                raise ValueError(f"Игрок «{name}» появился после предпросмотра. Повторите импорт.")
            if existing is None:
                c.execute("INSERT INTO players (name) VALUES (?)", (name,))
                player_id = c.lastrowid
                result.created_players.append(name)
            else:
                player_id = existing["id"]
            for day_num in range(1, 8):
                baseline = diff.db_snapshots.get(day_num)
                current = c.execute(
                    "SELECT total_score FROM day_scores WHERE season_id=? AND player_id=? AND day_number=?",
                    (preview.season_id, player_id, day_num),
                ).fetchone()
                current_value = current["total_score"] if current else None
                if current_value != baseline:
                    raise ValueError(f"Данные игрока «{name}» изменились после предпросмотра. Повторите импорт.")
                selected = diff.selected_snapshot(day_num)
                if selected == baseline or selected is None:
                    continue
                c.execute(
                    """INSERT INTO day_scores (season_id, player_id, day_number, total_score)
                       VALUES (?,?,?,?)
                       ON CONFLICT(season_id, player_id, day_number) DO UPDATE SET
                           total_score=excluded.total_score, recorded_at=datetime('now')""",
                    (preview.season_id, player_id, day_num, selected),
                )
            best = c.execute(
                "SELECT MAX(total_score) FROM day_scores WHERE season_id=? AND player_id=?",
                (preview.season_id, player_id),
            ).fetchone()[0]
            c.execute(
                """INSERT INTO scores (season_id, player_id, total_score, position)
                   VALUES (?,?,?,NULL)
                   ON CONFLICT(season_id, player_id) DO UPDATE SET
                       total_score=excluded.total_score, recorded_at=datetime('now')""",
                (preview.season_id, player_id, best or 0),
            )
            result.imported += 1
        set_audit_source(conn, "manual")
        conn.commit()
    except Exception:
        conn.rollback()
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    register_import(backup_path, "google", preview.season_id, database_path)
    return result
