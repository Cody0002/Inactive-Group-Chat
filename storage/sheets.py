"""
Google Sheets storage — optimized for a small server.

Notification-only model: the bot never asks anyone to authorize. Every group
it learns about (via the chat.created event or the audit backfill) is monitored,
and activity is judged purely from the admin audit log. There are no per-admin
OAuth tokens, so there is no admin_tokens tab.

Optimizations:
  - In-memory cache of each tab (TTL 60s) so repeated reads in one job
    don't re-hit the Sheets API.
  - Batch writes where possible (single API call instead of per-cell).
  - Cache invalidated on write so data stays correct.

Two tabs (auto-created):
  groups     — every group the bot knows about + its computed activity/state
  alert_log  — tracks alerts sent (drives the cooldown)
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Optional

import gspread

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CACHE_TTL = 60  # seconds

GROUPS_COLUMNS = [
    "chat_id", "group_name", "creator_id", "creator_name",
    "created_at",
    "member_count", "external",   # members at creation; "Yes"/"No" external users
    "last_activity_at", "days_inactive", "state",  # active / warning / inactive
    "last_checked_at", "last_activity_source", "last_activity_event_name",
]

ALERT_LOG_COLUMNS = [
    "timestamp", "admin_id", "chat_id", "group_name", "alert_type",
]

# Small key/value tab for bot state: base-build flag, member-sync watermark…
META_COLUMNS = ["key", "value"]

# ---- groups tab look & feel (see SheetsClient.format_groups_tab) ----
EXTERNAL_VALUES = ["No", "Yes"]          # dropdown values for the external column
CENTERED_COLUMNS = ["member_count", "external", "days_inactive", "state"]
DEFAULT_ROW_HEIGHT = 21                  # Sheets' own default — keeps rows uniform
DAYS_INACTIVE_RED = "#C5221F"
DAYS_INACTIVE_AMBER = "#B06000"


def _rgb(hex_color: str) -> dict[str, float]:
    """"#RRGGBB" -> the Sheets API's 0..1 colour dict."""
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255,
            "green": int(h[2:4], 16) / 255,
            "blue": int(h[4:6], 16) / 255}


class _CachedTab:
    """Wraps a gspread worksheet with a short-lived read cache."""

    def __init__(self, ws, columns: list[str]):
        self.ws = ws
        self.columns = columns
        self._cache: Optional[list[dict]] = None
        self._cache_at = 0.0

    def records(self, force: bool = False) -> list[dict]:
        now = time.time()
        if force or self._cache is None or (now - self._cache_at) > CACHE_TTL:
            self._cache = self.ws.get_all_records()
            self._cache_at = now
        return self._cache

    def invalidate(self):
        self._cache = None

    def find_row(self, key_col: str, key_val: str) -> Optional[int]:
        """Return 1-based row index for a matching record, or None."""
        for i, r in enumerate(self.records()):
            if str(r.get(key_col, "")) == str(key_val):
                return i + 2  # +2: header row + 0-index
        return None

    # Writes patch the cache in place instead of invalidating it — a re-read
    # after every write is what blows the Sheets read quota on large scans.
    def append(self, values: list):
        self.append_many([values])

    def append_many(self, rows: list[list]):
        """Append many rows in ONE API call."""
        if not rows:
            return
        self.ws.append_rows(rows, value_input_option="RAW")
        if self._cache is not None:
            for v in rows:
                self._cache.append(dict(zip(self.columns, v)))

    def update_row(self, row_idx: int, values: list):
        end = chr(ord("A") + len(values) - 1)
        self.ws.update(f"A{row_idx}:{end}{row_idx}", [values],
                       value_input_option="RAW")
        self._patch_cache(row_idx, dict(zip(self.columns, values)))

    def update_cells(self, row_idx: int, updates: dict[str, str]):
        """Update multiple cells in one row with a single API call."""
        self.update_rows_cells({row_idx: updates})

    def update_rows_cells(self, per_row: dict[int, dict[str, str]]):
        """Update cells across MANY rows in ONE batch API call."""
        cells = []
        for row_idx, updates in per_row.items():
            for col_name, val in updates.items():
                col_idx = self.columns.index(col_name)
                col_letter = chr(ord("A") + col_idx)
                cells.append({"range": f"{col_letter}{row_idx}",
                              "values": [[str(val)]]})
        if not cells:
            return
        self.ws.batch_update(cells, value_input_option="RAW")
        for row_idx, updates in per_row.items():
            self._patch_cache(row_idx, updates)

    def _patch_cache(self, row_idx: int, updates: dict[str, str]):
        if self._cache is None:
            return
        i = row_idx - 2
        if 0 <= i < len(self._cache):
            self._cache[i].update(updates)


class SheetsClient:
    _instance: Optional["SheetsClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init = False
        return cls._instance

    def __init__(self):
        if self._init:
            return
        # Google OAuth 2.0 user flow (installed-app). On first run this opens a
        # browser for consent, then caches the authorized-user token at
        # GOOGLE_TOKEN_PATH so later runs are non-interactive.
        gc = gspread.oauth(
            scopes=SCOPES,
            credentials_filename=settings.GOOGLE_CREDENTIALS_PATH,
            authorized_user_filename=settings.GOOGLE_TOKEN_PATH,
        )
        self.ss = gc.open_by_key(settings.SPREADSHEET_ID)
        self.groups = _CachedTab(self._ensure("groups", GROUPS_COLUMNS), GROUPS_COLUMNS)
        self.alerts = _CachedTab(self._ensure("alert_log", ALERT_LOG_COLUMNS), ALERT_LOG_COLUMNS)
        self.meta = _CachedTab(self._ensure("meta", META_COLUMNS), META_COLUMNS)
        # Set by format_groups_tab(); until then we don't know the dropdown
        # values, so appended rows can't be given their chips yet.
        self._event_names: Optional[list[str]] = None
        self._init = True

    def _ensure(self, name: str, headers: list[str]):
        try:
            ws = self.ss.worksheet(name)
            if ws.row_values(1) != headers:
                ws.update("A1", [headers])
        except gspread.WorksheetNotFound:
            ws = self.ss.add_worksheet(title=name, rows=2000, cols=len(headers))
            ws.update("A1", [headers])
        return ws

    # ---------- groups tab formatting ----------
    def format_groups_tab(self, event_names: list[str]) -> None:
        """(Re)apply the groups tab's look & feel. Idempotent — runs at startup.

        Rows appended through the API inherit neither data validation nor the
        wrap/alignment of the rows above them, which is how the tab ended up
        with dropdown chips on some rows and plain text on others. This lays
        the formatting back over the whole data range; _refresh_chips() then
        keeps newly appended rows in line between restarts.
        """
        self._event_names = list(event_names)
        rows = len(self.groups.records())
        end = rows + 1        # exclusive row index; +1 skips the header
        all_rows = self.groups.ws.row_count

        # Chips stop at the last data row — validating the empty rows below
        # would litter the rest of the tab with blank dropdowns. Everything
        # else covers the whole sheet, so the empty area matches the table and
        # future rows are already formatted.
        requests = self._chip_requests(end)
        requests += [
            # group_name was the only WRAP column, so one long name made its
            # row taller than every other. CLIP keeps all rows one line high.
            {"repeatCell": {
                "range": self._col_range("group_name", 1, all_rows),
                "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                "fields": "userEnteredFormat.wrapStrategy"}},
            # Row 1 keeps its taller header height — start at row 2.
            {"updateDimensionProperties": {
                "range": {"sheetId": self.groups.ws.id, "dimension": "ROWS",
                          "startIndex": 1, "endIndex": all_rows},
                "properties": {"pixelSize": DEFAULT_ROW_HEIGHT},
                "fields": "pixelSize"}},
        ]
        # Short status columns read better centred, and it lines the chips up
        # under their headers (row 0 = header, so it moves too).
        requests += [
            {"repeatCell": {
                "range": self._col_range(name, 0, all_rows),
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.horizontalAlignment"}}
            for name in CENTERED_COLUMNS
        ]
        requests += self._days_inactive_cf_requests()

        self.ss.batch_update({"requests": requests})
        logger.info("groups tab formatted — %s data row(s), %s request(s)",
                    rows, len(requests))

    def _col_range(self, col_name: str, start_row: int, end_row: int) -> dict:
        """A single-column GridRange (0-based, end-exclusive)."""
        i = GROUPS_COLUMNS.index(col_name)
        return {"sheetId": self.groups.ws.id,
                "startRowIndex": start_row,
                "endRowIndex": max(end_row, start_row + 1),
                "startColumnIndex": i, "endColumnIndex": i + 1}

    def _chip_requests(self, end_row: int) -> list[dict]:
        """Dropdown ("chip") validation for the two enum columns.

        Only the rows that hold data get it — validating the empty rows below
        would litter the rest of the tab with blank dropdowns.
        """
        lists = {
            "external": EXTERNAL_VALUES,
            # "" keeps blank cells valid: a group with no audit event yet.
            "last_activity_event_name": [""] + sorted(self._event_names or []),
        }
        return [
            {"setDataValidation": {
                "range": self._col_range(name, 1, end_row),
                "rule": {
                    "condition": {"type": "ONE_OF_LIST",
                                  "values": [{"userEnteredValue": v} for v in values]},
                    "strict": True, "showCustomUi": True}}}
            for name, values in lists.items()
        ]

    def _refresh_chips(self) -> None:
        """Extend the dropdown chips over rows appended since the last format."""
        if self._event_names is None:
            return  # format_groups_tab() hasn't run — nothing to extend yet
        try:
            self.ss.batch_update({
                "requests": self._chip_requests(len(self.groups.records()) + 1)})
        except Exception as e:
            logger.warning(f"Could not extend dropdown chips to new rows: {e}")

    def _days_inactive_cf_requests(self) -> list[dict]:
        """Colour days_inactive at / approaching the inactivity threshold.

        Added only when the exact rule isn't on the tab already, so restarts
        never pile up duplicates and hand-made rules are left alone. The two
        conditions are mutually exclusive, so rule order doesn't matter.
        VALUE() because the column holds text (everything is written RAW).
        """
        letter = chr(ord("A") + GROUPS_COLUMNS.index("days_inactive"))
        cell = f"${letter}2"
        threshold = settings.INACTIVITY_THRESHOLD_DAYS
        near = max(threshold - settings.NEAR_INACTIVE_DAYS, 1)
        wanted = [
            (f'=AND({cell}<>"",VALUE({cell})>={threshold})', DAYS_INACTIVE_RED),
            (f'=AND({cell}<>"",VALUE({cell})>={near},VALUE({cell})<{threshold})',
             DAYS_INACTIVE_AMBER),
        ]
        existing = self._custom_formula_rules()
        # Full-height range (like the state rules) so it never needs widening.
        full = self._col_range("days_inactive", 1, self.groups.ws.row_count)
        return [
            {"addConditionalFormatRule": {
                "index": 0,
                "rule": {
                    "ranges": [full],
                    "booleanRule": {
                        "condition": {"type": "CUSTOM_FORMULA",
                                      "values": [{"userEnteredValue": formula}]},
                        "format": {"textFormat": {
                            "bold": True,
                            "foregroundColorStyle": {"rgbColor": _rgb(color)}}}}}}}
            for formula, color in wanted if formula not in existing
        ]

    def _custom_formula_rules(self) -> set[str]:
        """CUSTOM_FORMULA conditions already on the groups tab."""
        meta = self.ss.fetch_sheet_metadata(params={
            "fields": "sheets(properties(sheetId),conditionalFormats)"})
        formulas = set()
        for sh in meta.get("sheets", []):
            if sh.get("properties", {}).get("sheetId") != self.groups.ws.id:
                continue
            for rule in sh.get("conditionalFormats", []):
                cond = rule.get("booleanRule", {}).get("condition", {})
                if cond.get("type") == "CUSTOM_FORMULA":
                    for v in cond.get("values", []):
                        formulas.add(v.get("userEnteredValue", ""))
        return formulas

    # ---------- groups ----------
    def log_new_group(self, chat_id: str, group_name: str,
                      creator_id: str, creator_name: str,
                      created_at: str = "", member_count: str = "",
                      external: str = "") -> bool:
        """Record a group. Returns False if it was already logged.

        created_at defaults to now (live chat.created event); pass an explicit
        ISO timestamp when backfilling historical groups from the audit log.
        member_count / external come from the audit record (blank via webhook).
        """
        if self.groups.find_row("chat_id", chat_id):
            return False
        created = created_at or datetime.now(timezone.utc).isoformat()
        self.groups.append([
            chat_id, group_name, creator_id, creator_name,
            created, member_count, external,
            created, "", "active", "", "created_at_fallback", "",
        ])
        self._refresh_chips()
        return True

    def log_new_groups_bulk(self, recs: list[dict]) -> list[str]:
        """Record many groups in ONE append call (used by the audit scan /
        base build). Each rec needs chat_id/group_name/creator_id/creator_name
        and may carry created_at/members/external. Returns new chat_ids."""
        existing = {str(r.get("chat_id", "")) for r in self.groups.records()}
        rows, added = [], []
        for rec in recs:
            chat_id = rec["chat_id"]
            if chat_id in existing:
                continue
            existing.add(chat_id)
            created = rec.get("created_at") or datetime.now(timezone.utc).isoformat()
            rows.append([
                chat_id, rec.get("group_name", ""), rec.get("creator_id", ""),
                rec.get("creator_name", ""),
                created, rec.get("members", ""), rec.get("external", ""),
                created, "", "active", "", "created_at_fallback", "",
            ])
            added.append(chat_id)
        self.groups.append_many(rows)
        if rows:
            self._refresh_chips()
        return added

    def set_group_meta(self, chat_id: str, member_count: str = "",
                       external: str = "") -> None:
        """Fill in member_count / external for an existing group, but only for
        fields that are currently blank (so webhook-first rows get enriched by
        the audit scan without overwriting known values)."""
        row = self.groups.find_row("chat_id", chat_id)
        if not row:
            return
        current = None
        for r in self.groups.records():
            if str(r.get("chat_id", "")) == str(chat_id):
                current = r
                break
        updates = {}
        if member_count and not (current or {}).get("member_count"):
            updates["member_count"] = member_count
        if external and not (current or {}).get("external"):
            updates["external"] = external
        if updates:
            self.groups.update_cells(row, updates)

    def get_all_groups(self) -> list[dict]:
        return self.groups.records()

    def bulk_update_group_activity(self, changes: dict[str, dict[str, str]]):
        """Write activity fields for many groups in ONE batch call.
        changes: chat_id -> {last_activity_at, days_inactive, state,
                             last_activity_source, last_activity_event_name}
        May also carry group_name when a group was renamed in Lark. An empty
        field dict still stamps last_checked_at."""
        now_iso = datetime.now(timezone.utc).isoformat()
        per_row: dict[int, dict[str, str]] = {}
        for chat_id, fields in changes.items():
            row = self.groups.find_row("chat_id", chat_id)
            if not row:
                continue
            per_row[row] = {**fields, "last_checked_at": now_iso}
        self.groups.update_rows_cells(per_row)

    def adjust_member_counts(self, deltas: dict[str, int]) -> int:
        """Apply member add/remove deltas (from audit events) to member_count
        in ONE batch call. Rows with an unknown (blank) base count are skipped.
        Returns rows changed."""
        per_row: dict[int, dict[str, str]] = {}
        for i, r in enumerate(self.groups.records()):
            delta = deltas.get(str(r.get("chat_id", "")), 0)
            if not delta:
                continue
            try:
                base = int(r.get("member_count"))
            except (TypeError, ValueError):
                continue  # unknown base — can't apply a delta meaningfully
            per_row[i + 2] = {"member_count": str(max(base + delta, 1))}
        self.groups.update_rows_cells(per_row)
        return len(per_row)

    # ---------- meta (key/value bot state) ----------
    def meta_get(self, key: str, default: str = "") -> str:
        for r in self.meta.records():
            if str(r.get("key", "")) == key:
                return str(r.get("value", ""))
        return default

    def meta_set(self, key: str, value: str):
        row = self.meta.find_row("key", key)
        if row:
            self.meta.update_row(row, [key, str(value)])
        else:
            self.meta.append([key, str(value)])

    # ---------- alert log (with self-cleanup) ----------
    def was_recently_alerted(self, chat_id: str, alert_type: str,
                             cooldown_days: int) -> bool:
        cutoff = datetime.now(timezone.utc).timestamp() - cooldown_days * 86400
        for r in self.alerts.records():
            if r.get("chat_id") == chat_id and r.get("alert_type") == alert_type:
                try:
                    if datetime.fromisoformat(r["timestamp"]).timestamp() > cutoff:
                        return True
                except Exception:
                    continue
        return False

    def log_alert(self, admin_id: str, chat_id: str, group_name: str, alert_type: str):
        self.alerts.append([
            datetime.now(timezone.utc).isoformat(),
            admin_id, chat_id, group_name, alert_type,
        ])

    def prune_old_alerts(self, keep_days: int = 30):
        """Delete alert_log rows older than keep_days to keep the sheet small."""
        cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
        records = self.alerts.records(force=True)
        keep_rows = []
        for r in records:
            try:
                if datetime.fromisoformat(r["timestamp"]).timestamp() > cutoff:
                    keep_rows.append([r.get(c, "") for c in ALERT_LOG_COLUMNS])
            except Exception:
                keep_rows.append([r.get(c, "") for c in ALERT_LOG_COLUMNS])
        if len(keep_rows) == len(records):
            return  # nothing to prune
        # Rewrite the tab: clear then re-add header + kept rows
        self.alerts.ws.clear()
        self.alerts.ws.update("A1", [ALERT_LOG_COLUMNS] + keep_rows,
                              value_input_option="RAW")
        self.alerts.invalidate()
        logger.info(f"Pruned alert_log: {len(records) - len(keep_rows)} old rows removed")


def get_sheets() -> SheetsClient:
    return SheetsClient()
