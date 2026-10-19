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
    # state: active / warning / inactive / deleted. "deleted" is terminal —
    # the row is FROZEN: activity is never recomputed and it never alerts again.
    # For a deleted group last_activity_at holds the dissolution time (being
    # dissolved IS the last thing that happened to it).
    "last_activity_at", "days_inactive", "state",
    "last_checked_at", "last_activity_source", "last_activity_event_name",
    "deleted_by",                 # who dissolved it, from im_delete_chat
    # Ownership (ownership.py). owner_id / owner_name = who owns it NOW;
    # owner_status says how we know:
    #   creator      the creator still owns it (default at creation)
    #   transferred  im_forward_chatadmin named the new owner (owner_id from
    #                the drawer key im_forward_adminlist; owner_name is
    #                back-filled once we've seen that person in the log)
    #   named        a person told us — picker card or admin edit
    #   left         the current owner left the organisation (leavers.py)
    #   unknown      a transfer was seen without a new-owner id (not observed)
    # owner_transferred_at / _from record the last hand-over itself.
    "owner_transferred_at", "owner_transferred_from",
    "owner_id", "owner_name",
    # Set from user_active_exit_team (leavers.py) when the creator left the
    # company. Never DM this creator. Blank is NOT proof they are still here —
    # only self-initiated exits emit the event.
    "creator_left_at",
    # New columns go at the END — _ensure() only rewrites the header row, so
    # inserting one in the middle would shift every existing row's data.
    "owner_status",
    "last_activity_by",           # operator of the newest activity event
]

STATE_DELETED = "deleted"

ALERT_LOG_COLUMNS = [
    "timestamp", "admin_id", "chat_id", "group_name", "alert_type",
]

# Small key/value tab for bot state: base-build flag, member-sync watermark…
META_COLUMNS = ["key", "value"]

# ---- groups tab look & feel (see SheetsClient.format_groups_tab) ----
EXTERNAL_VALUES = ["No", "Yes"]          # dropdown values for the external column
STATE_VALUES = ["active", "warning", "inactive", "deleted"]
OWNER_CREATOR, OWNER_TRANSFERRED, OWNER_NAMED, OWNER_LEFT, OWNER_UNKNOWN = (
    "creator", "transferred", "named", "left", "unknown")
OWNER_STATUS_VALUES = [OWNER_CREATOR, OWNER_TRANSFERRED, OWNER_NAMED,
                       OWNER_LEFT, OWNER_UNKNOWN]
CENTERED_COLUMNS = ["member_count", "external", "days_inactive", "state",
                    "owner_status"]
DEFAULT_ROW_HEIGHT = 21                  # Sheets' own default — keeps rows uniform
DAYS_INACTIVE_RED = "#C5221F"
DAYS_INACTIVE_AMBER = "#B06000"


def _group_row(**fields) -> list:
    """A groups-tab row in GROUPS_COLUMNS order; unnamed columns stay blank.
    Building rows by name is what keeps the two append paths aligned with the
    header when a column is added."""
    return [fields.get(c, "") for c in GROUPS_COLUMNS]


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
            # Every cell as text — the same type the writes patch into the
            # cache. gspread's default turns a number-only group name into an
            # int, and the first .strip() on it killed every refresh from
            # 2026-09-22 to 2026-10-19.
            self._cache = self.ws.get_all_records(numericise_ignore=["all"])
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
                # A tab created by an older build is too narrow for newly added
                # columns — widen it first or the header write exceeds the grid.
                if ws.col_count < len(headers):
                    ws.add_cols(len(headers) - ws.col_count)
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
            "state": STATE_VALUES,
            "owner_status": [""] + OWNER_STATUS_VALUES,
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
        self.groups.append(_group_row(
            chat_id=chat_id, group_name=group_name, creator_id=creator_id,
            creator_name=creator_name, created_at=created,
            member_count=member_count, external=external,
            last_activity_at=created, state="active",
            last_activity_source="created_at_fallback",
            owner_id=creator_id, owner_name=creator_name,
            owner_status=OWNER_CREATOR))
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
            rows.append(_group_row(
                chat_id=chat_id, group_name=rec.get("group_name", ""),
                creator_id=rec.get("creator_id", ""),
                creator_name=rec.get("creator_name", ""), created_at=created,
                member_count=rec.get("members", ""),
                external=rec.get("external", ""),
                last_activity_at=created, state="active",
                last_activity_source="created_at_fallback",
                owner_id=rec.get("creator_id", ""),
                owner_name=rec.get("creator_name", ""),
                owner_status=OWNER_CREATOR))
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

    def get_live_groups(self) -> list[dict]:
        """Every group that still exists. Deleted groups are excluded from all
        monitoring — no activity recompute, no alerts, no report rows."""
        return [g for g in self.groups.records()
                if str(g.get("state", "")).strip().lower() != STATE_DELETED]

    def mark_groups_deleted(self, deletions: dict[str, dict[str, str]]) -> list[str]:
        """Tag groups as deleted from im_delete_chat audit records, in ONE batch.

        deletions: chat_id -> {deleted_at, deleted_by, group_name}

        state="deleted" is the tag itself — terminal, and checked by
        get_live_groups() so the row is never recomputed or alerted on again.
        The dissolution time goes into last_activity_at, because being
        dissolved is the last thing that happened to the group.

        Only touches rows that aren't already consistent (state deleted AND
        deleted_by recorded), so re-scanning the same audit window is idempotent
        and costs no writes. A row that has drifted either way — state tagged by
        hand with no deleted_by, or deleted_by written with the state left
        behind — is repaired here, as long as it is still inside the window.
        Rows older than that are the one-time repair_deleted_states() pass's job.

        Returns the chat_ids newly tagged (rows that weren't deleted before).
        """
        now = datetime.now(timezone.utc)
        per_row: dict[int, dict[str, str]] = {}
        tagged: list[str] = []
        repaired = 0
        for i, r in enumerate(self.groups.records()):
            chat_id = str(r.get("chat_id", ""))
            rec = deletions.get(chat_id)
            if not rec:
                continue
            was_deleted = str(r.get("state", "")).strip().lower() == STATE_DELETED
            if was_deleted and str(r.get("deleted_by", "")).strip():
                continue  # already tagged and consistent
            deleted_at = rec.get("deleted_at", "")
            fields = {
                "state": STATE_DELETED,
                "deleted_by": rec.get("deleted_by", ""),
                "last_activity_source": "audit_log",
                "last_activity_event_name": "im_delete_chat",
            }
            if deleted_at:
                fields["last_activity_at"] = deleted_at
                try:
                    dt = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
                    fields["days_inactive"] = str((now - dt).days)
                except ValueError:
                    pass
            # The audit record carries the name the group had when it was
            # deleted — keep it if the sheet's is blank/stale.
            if rec.get("group_name") and not r.get("group_name"):
                fields["group_name"] = rec["group_name"]
            per_row[i + 2] = fields
            if was_deleted:
                repaired += 1   # state was already right — filling the gaps
            else:
                tagged.append(chat_id)
        if per_row:
            self.groups.update_rows_cells(per_row)
        if repaired:
            logger.info("Completed %s already-deleted row(s) from the audit log",
                        repaired)
        return tagged

    def repair_deleted_states(self) -> list[str]:
        """One-time repair: every row with deleted_by filled in but state not
        yet "deleted" gets tagged, in ONE batch. Returns the chat_ids fixed.

        deleted_by is only ever written by mark_groups_deleted(), so a non-empty
        deleted_by is itself proof the group was dissolved — the state column
        just never caught up (rows tagged before that write included state, or
        hand-edited in the sheet). Until it does, get_live_groups() keeps
        handing the row to the checker, days_inactive keeps climbing and the
        group re-alerts forever — for a group that no longer exists.

        This is a full-table scan, which is why checker.run_deleted_state_repair()
        gates it behind a meta flag and runs it once. Steady state is
        mark_groups_deleted()'s job, and that only touches the rows in the
        current audit window.
        """
        per_row: dict[int, dict[str, str]] = {}
        fixed: list[str] = []
        # force=True: this runs once, and it has to see manual sheet edits
        # rather than whatever the 60s cache was holding.
        for i, r in enumerate(self.groups.records(force=True)):
            if not str(r.get("deleted_by", "")).strip():
                continue
            if str(r.get("state", "")).strip().lower() == STATE_DELETED:
                continue  # already consistent
            per_row[i + 2] = {"state": STATE_DELETED}
            fixed.append(str(r.get("chat_id", "")))
        if per_row:
            self.groups.update_rows_cells(per_row)
        return fixed

    # ---------- ownership ----------
    def init_owner_columns(self) -> int:
        """One-time: give every row without an owner_status its owner.

        The creator owns a group until the audit log says otherwise, so rows
        that predate the owner columns get owner_id / owner_name = creator and
        owner_status = "creator". A row where someone already typed an
        owner_id by hand is respected and becomes "named". Full-table, ONE
        batch, gated by a meta flag in checker.run_ownership_backfill().
        Returns the rows set.
        """
        per_row: dict[int, dict[str, str]] = {}
        for i, r in enumerate(self.groups.records(force=True)):
            if str(r.get("owner_status", "")).strip():
                continue
            if str(r.get("state", "")).strip().lower() == STATE_DELETED:
                continue
            if str(r.get("owner_id", "")).strip():
                per_row[i + 2] = {"owner_status": OWNER_NAMED}
            else:
                per_row[i + 2] = {"owner_id": str(r.get("creator_id", "")),
                                  "owner_name": str(r.get("creator_name", "")),
                                  "owner_status": OWNER_CREATOR}
        if per_row:
            self.groups.update_rows_cells(per_row)
        return len(per_row)

    def mark_owner_transferred(self, transfers: dict[str, dict[str, str]],
                               people: dict[str, str] | None = None) -> list[str]:
        """Record ownership transfers from im_forward_chatadmin audit records,
        in ONE batch. transfers: chat_id -> {transferred_at, transferred_from,
        new_owner_id}. people: user_id -> display name for naming the new
        owner; the groups tab's own creators are always consulted too.

        Only the latest transfer per chat is kept: a row already holding this
        transfer (or a later one) is skipped, so re-scanning an overlapping
        window is idempotent and costs no writes. Deleted rows are frozen.

        With a new_owner_id the log is authoritative: owner_id is set, the
        name filled if we know the person (else left blank for
        fill_owner_names() to complete later) and owner_status becomes
        "transferred" — whatever a human had typed before. Without one (not
        observed: 155/155 items carried it, 2026-09-18) a hand-named owner is
        kept on a first transfer and cleared on a newer one, status "unknown".

        Returns the chat_ids whose transfer was newly recorded or updated.
        """
        names = {str(r.get("creator_id", "")): str(r.get("creator_name", ""))
                 for r in self.groups.records() if r.get("creator_id")}
        names.update({k: v for k, v in (people or {}).items() if k and v})
        per_row: dict[int, dict[str, str]] = {}
        recorded: list[str] = []
        for i, r in enumerate(self.groups.records()):
            chat_id = str(r.get("chat_id", ""))
            rec = transfers.get(chat_id)
            if not rec:
                continue
            if str(r.get("state", "")).strip().lower() == STATE_DELETED:
                continue  # frozen — nobody owns a dissolved group
            new_at = str(rec.get("transferred_at", "")).strip()
            have_at = str(r.get("owner_transferred_at", "")).strip()
            if not new_at or (have_at and _iso_key(have_at) >= _iso_key(new_at)):
                continue  # already know about this transfer, or a later one
            fields = {"owner_transferred_at": new_at,
                      "owner_transferred_from": rec.get("transferred_from", "")}
            new_owner = str(rec.get("new_owner_id", "")).strip()
            status = str(r.get("owner_status", "")).strip().lower()
            if new_owner:
                fields.update({"owner_id": new_owner,
                               "owner_name": names.get(new_owner, ""),
                               "owner_status": OWNER_TRANSFERRED})
            elif status == OWNER_NAMED and not have_at:
                pass  # first transfer we see, and a person already named the owner
            else:
                fields.update({"owner_id": "", "owner_name": "",
                               "owner_status": OWNER_UNKNOWN})
            per_row[i + 2] = fields
            recorded.append(chat_id)
        if per_row:
            self.groups.update_rows_cells(per_row)
        return recorded

    def fill_owner_names(self, people: dict[str, str]) -> int:
        """Name owners we only know by user_id, from people seen as audit
        operators this run. ONE batch; returns the rows filled."""
        per_row: dict[int, dict[str, str]] = {}
        for i, r in enumerate(self.groups.records()):
            if str(r.get("owner_name", "")).strip():
                continue
            name = people.get(str(r.get("owner_id", "")).strip())
            if name:
                per_row[i + 2] = {"owner_name": name}
        if per_row:
            self.groups.update_rows_cells(per_row)
        return len(per_row)

    def mark_creators_left(self, leavers: dict[str, dict[str, str]]) -> list[str]:
        """Record people who left the organisation, in ONE batch.
        leavers: user_id -> {left_at, name}.

        Two facts per live row, each written only when it is news:
          - creator_left_at   stamped when the CREATOR is a leaver
          - owner_status=left set when the CURRENT owner (owner_id, falling
                              back to creator_id) is a leaver — the person a
                              cleanup ask would otherwise go to
        Deleted rows are frozen. Idempotent across overlapping audit windows.
        Returns the chat_ids touched.
        """
        per_row: dict[int, dict[str, str]] = {}
        marked: list[str] = []
        for i, r in enumerate(self.groups.records()):
            if str(r.get("state", "")).strip().lower() == STATE_DELETED:
                continue  # frozen
            fields: dict[str, str] = {}
            creator = str(r.get("creator_id", "")).strip()
            rec = leavers.get(creator)
            if rec and not str(r.get("creator_left_at", "")).strip():
                fields["creator_left_at"] = rec.get("left_at", "")
            owner = str(r.get("owner_id", "")).strip() or creator
            if (owner in leavers
                    and str(r.get("owner_status", "")).strip().lower() != OWNER_LEFT):
                fields["owner_status"] = OWNER_LEFT
            if fields:
                per_row[i + 2] = fields
                marked.append(str(r.get("chat_id", "")))
        if per_row:
            self.groups.update_rows_cells(per_row)
        return marked

    def set_group_owner(self, chat_id: str, owner_id: str, owner_name: str = "") -> bool:
        """Record who currently owns a group, as told by a person — the "who
        owns it now?" picker or an admin. owner_status becomes "named".
        Returns False if the group is unknown or deleted."""
        for i, r in enumerate(self.groups.records()):
            if str(r.get("chat_id", "")) != str(chat_id):
                continue
            if str(r.get("state", "")).strip().lower() == STATE_DELETED:
                return False
            self.groups.update_cells(i + 2, {"owner_id": owner_id,
                                             "owner_name": owner_name,
                                             "owner_status": OWNER_NAMED})
            return True
        return False

    def bulk_update_group_activity(self, changes: dict[str, dict[str, str]]):
        """Write activity fields for many groups in ONE batch call.
        changes: chat_id -> {last_activity_at, days_inactive, state,
                             last_activity_source, last_activity_event_name}
        May also carry group_name when a group was renamed in Lark. An empty
        field dict still stamps last_checked_at.

        Rows marked deleted are skipped outright. "deleted" is terminal, and
        enforcing that HERE — at the single choke point every activity write
        passes through — is what makes it stick. Relying only on callers to
        filter with get_live_groups() means one missed filter silently
        resurrects a dissolved group and restarts the forever-alert bug.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        state_by_row = {
            i + 2: str(r.get("state", "")).strip().lower()
            for i, r in enumerate(self.groups.records())
        }
        per_row: dict[int, dict[str, str]] = {}
        skipped = 0
        for chat_id, fields in changes.items():
            row = self.groups.find_row("chat_id", chat_id)
            if not row:
                continue
            if state_by_row.get(row) == STATE_DELETED:
                skipped += 1
                continue
            per_row[row] = {**fields, "last_checked_at": now_iso}
        if skipped:
            logger.info("Skipped %s deleted row(s) — frozen", skipped)
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
            if str(r.get("state", "")).strip().lower() == STATE_DELETED:
                continue  # frozen — a dissolved group has no members to count
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


def _iso_key(value: str):
    """Sortable key for an ISO timestamp cell; falls back to the raw string for
    hand-edited values that don't parse, so a comparison never raises."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (0, dt.timestamp())
    except ValueError:
        return (1, value)


def get_sheets() -> SheetsClient:
    return SheetsClient()
