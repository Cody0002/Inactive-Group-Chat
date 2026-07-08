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
                             last_activity_source, last_activity_event_name}"""
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
