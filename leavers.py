"""
Leaver detection from the admin audit log (`user_active_exit_team`,
module 6 "Account Activity" — Lark's description: "Left organization").

Why the cleanup flow cares: a private message to someone who has left the
company never lands. Knowing the creator left lets the Monday approval card
route their groups to the "needs an owner's name" block instead of ticking
them for a DM, and lets Friday's list mark them.

Measured against the live tenant 2026-09-18: `operator_value` and
`objects[0].object_value` (type 1 = user) both hold the leaver's bare 8-char
user_id — the same id type the sheet stores in creator_id — so rows match
directly with no lookup. About one event per 30 days here.

Coverage is PARTIAL: only self-initiated exits emit this event. An account an
admin deactivates may not, so a blank creator_left_at is not proof the creator
is still here — a failed delivery stays the fallback signal. The contact
scope (`contact:user.base:readonly`) would close that gap.

Same window mechanics as deletions.py: 30 days per query, 180 days retention.
"""
from __future__ import annotations
from datetime import datetime, timezone

from deletions import _operator_name, sweep_latest
from storage.sheets import get_sheets
from utils.logger import get_logger

logger = get_logger(__name__)

LEAVE_EVENT = "user_active_exit_team"
ACCOUNT_EVENT_MODULE = 6
USER_OBJECT_TYPE = "1"


def parse_leaver(item: dict) -> dict | None:
    """Extract {user_id, name, event_time, left_at} from one
    user_active_exit_team audit item."""
    user_id = str(item.get("operator_value") or "").strip()
    if not user_id:
        for obj in item.get("objects", []) or []:
            if (str(obj.get("object_type", "")) == USER_OBJECT_TYPE
                    and obj.get("object_value")):
                user_id = str(obj["object_value"]).strip()
                break
    if not user_id:
        return None
    try:
        ts = int(item.get("event_time") or 0)
    except (TypeError, ValueError):
        ts = 0
    when = (datetime.fromtimestamp(ts, tz=timezone.utc)
            if ts else datetime.now(timezone.utc))
    return {
        "user_id": user_id,
        "name": _operator_name(item),
        "event_time": ts,
        "left_at": when.isoformat(),
    }


async def collect_leavers(lookback_days: int) -> dict[str, dict]:
    """People who left in the last `lookback_days`, keyed by user_id."""
    return await sweep_latest(LEAVE_EVENT, lookback_days, parse_leaver,
                              key="user_id", event_module=ACCOUNT_EVENT_MODULE)


async def scan_leavers(lookback_days: int) -> list[str]:
    """Scan for leavers and stamp creator_left_at on the groups they created.

    Returns the chat_ids newly marked. Idempotent — rows already stamped are
    skipped, so re-scanning an overlapping hourly window costs no writes.
    """
    found = await collect_leavers(lookback_days)
    if not found:
        return []
    marked = get_sheets().mark_creators_left(found)
    if marked:
        logger.info("Creator left the company: %s tracked group(s) now routed "
                    "away from their creator", len(marked))
    return marked
