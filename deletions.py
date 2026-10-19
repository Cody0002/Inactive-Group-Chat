"""
Deleted-group detection from the admin audit log (`im_delete_chat`).

Lark's own definition: "Dissolved a group chat." It is the authoritative,
retroactive signal that a group is gone — the `im.chat.disbanded_v1` webhook
only fires for chats the bot is a MEMBER of, and this bot never joins groups,
so the audit log is the only channel that sees these.

A dissolved group gets `state = "deleted"` — terminal, and the row is FROZEN:
the checker stops recomputing its activity and it never alerts or appears in a
report again. Its `last_activity_at` becomes the dissolution time (being
dissolved IS the last thing that happened to it) and `deleted_by` records who
did it. Without this, a deleted group sits in the sheet accumulating
days_inactive forever and starts re-alerting every REALERT_COOLDOWN_DAYS once it
crosses the threshold — for a group that no longer exists.

Window limits (measured against the live tenant, 2026-08-12):
  - one query may span at most 30 days   → AUDIT_MAX_WINDOW_DAYS
  - the tenant retains 180 days of audit → older windows return
    TIME_CHECK_NOT_VALID (code 1050001)
So a full sweep is six chained 30-day windows.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from lark import api
from storage.sheets import get_sheets
from utils.logger import get_logger

logger = get_logger(__name__)

DELETE_EVENT = "im_delete_chat"
MESSENGER_EVENT_MODULE = 2
CHAT_OBJECT_TYPE = "4"
AUDIT_PAGE_SIZE = 200

AUDIT_MAX_WINDOW_DAYS = 30    # hard API limit for a single query
AUDIT_RETENTION_DAYS = 180    # how far back the tenant keeps audit data
# Lark's "time range invalid" code — returned when a window predates retention.
TIME_RANGE_INVALID = 1050001


def _operator_name(item: dict) -> str:
    detail = (item.get("operator_detail", {}) or {}).get("operator_name", {}) or {}
    i18n = detail.get("i18n_value", {}) or {}
    return i18n.get("en_us") or detail.get("default_name") or ""


def _chat_from_objects(item: dict) -> tuple[str, str]:
    for obj in item.get("objects", []) or []:
        if str(obj.get("object_type", "")) == CHAT_OBJECT_TYPE:
            value = obj.get("object_value", "")
            if isinstance(value, str) and value.startswith("oc_"):
                return value, str(obj.get("object_name") or "").strip()
    return "", ""


def parse_deleted_group(item: dict) -> dict | None:
    """Extract {chat_id, group_name, deleted_at, deleted_by} from one
    im_delete_chat audit item."""
    chat_id, name = _chat_from_objects(item)
    if not chat_id:
        return None
    try:
        ts = int(item.get("event_time") or 0)
    except (TypeError, ValueError):
        ts = 0
    deleted_dt = (datetime.fromtimestamp(ts, tz=timezone.utc)
                  if ts else datetime.now(timezone.utc))
    return {
        "chat_id": chat_id,
        "group_name": name,
        "event_time": ts,
        "deleted_at": deleted_dt.isoformat(),
        "deleted_by": _operator_name(item),
    }


async def _fetch_window(oldest: int, latest: int,
                        event_name: str = DELETE_EVENT,
                        event_module: int = MESSENGER_EVENT_MODULE) -> tuple[list[dict], bool]:
    """One <=30-day window of one event type.
    Returns (items, window_was_within_retention)."""
    items: list[dict] = []
    page_token = None
    while True:
        try:
            data = await api.get_behavior_audit_logs(
                oldest=oldest, latest=latest,
                event_name=event_name, event_module=event_module,
                page_size=AUDIT_PAGE_SIZE, page_token=page_token)
        except Exception as e:
            if str(TIME_RANGE_INVALID) in str(e) or "TIME_CHECK_NOT_VALID" in str(e):
                return items, False   # past the retention edge — stop sweeping
            logger.warning(f"{event_name} fetch failed: {e}")
            return items, True
        items.extend(i for i in data.get("items", [])
                     if i.get("event_name") == event_name)
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return items, True


async def sweep_latest(event_name: str, lookback_days: int, parse, *,
                       key: str = "chat_id",
                       event_module: int = MESSENGER_EVENT_MODULE) -> dict[str, dict]:
    """The newest `event_name` audit item per `key` over `lookback_days`.

    `parse(item)` turns an audit item into a record carrying at least `key`
    and event_time (or None to skip it). Chains 30-day windows so a lookback
    beyond the per-query limit still works (a full 180-day sweep is six
    windows) and stops early at the retention edge. Shared by the deleted-
    group scan and the ownership scans (ownership.py).
    """
    lookback_days = max(1, min(lookback_days, AUDIT_RETENTION_DAYS))
    now = datetime.now(timezone.utc)
    floor = now - timedelta(days=lookback_days)

    found: dict[str, dict] = {}
    window_end = now
    windows = 0
    while window_end > floor:
        window_start = max(window_end - timedelta(days=AUDIT_MAX_WINDOW_DAYS), floor)
        items, in_retention = await _fetch_window(
            int(window_start.timestamp()), int(window_end.timestamp()),
            event_name, event_module)
        windows += 1
        for item in items:
            rec = parse(item)
            if not rec:
                continue
            # Keep the newest event per key — audit rows can duplicate across
            # overlapping windows, and only the latest transfer/deletion counts.
            prev = found.get(rec[key])
            if not prev or rec["event_time"] > prev["event_time"]:
                found[rec[key]] = rec
        if not in_retention:
            logger.info("Reached audit retention edge after %s window(s)", windows)
            break
        window_end = window_start

    logger.info("%s scan: %s window(s), %s %s(s) seen",
                event_name, windows, len(found), key)
    return found


async def collect_deleted_groups(lookback_days: int) -> dict[str, dict]:
    """Every group dissolved in the last `lookback_days`, keyed by chat_id."""
    return await sweep_latest(DELETE_EVENT, lookback_days, parse_deleted_group)


async def scan_deleted_groups(lookback_days: int) -> list[str]:
    """Scan for dissolved groups and tag the tracked ones as deleted.

    Returns the chat_ids newly tagged. Idempotent — rows already tagged are
    skipped, so re-scanning an overlapping window costs no writes.
    """
    found = await collect_deleted_groups(lookback_days)
    if not found:
        return []
    tagged = get_sheets().mark_groups_deleted(found)
    for chat_id in tagged:
        rec = found[chat_id]
        logger.info("Group deleted: %r by %s on %s",
                    rec["group_name"] or chat_id, rec["deleted_by"] or "?",
                    rec["deleted_at"][:10])
    if tagged:
        logger.info("Tagged %s group(s) as deleted — no longer tracked", len(tagged))
    return tagged
