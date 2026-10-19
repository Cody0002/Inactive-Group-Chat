"""
Who owns a group — from the admin audit log, without joining anything.

im_forward_chatadmin — Lark: "Set group owner". Only the OWNER can dissolve a
group, so once ownership has moved the creator is the wrong person to ask
about cleaning it up. Measured against the live tenant on 2026-09-18 (155
items over the full 180-day retention, every one of them):
    objects[] type 4                          the chat and its name
    operator_value / operator_detail          the OUTGOING owner
    common_drawers → "im_forward_adminlist"   the NEW owner's user_id
                                              ("ID of the newly assigned group owner")
    common_drawers → "im_chat_opstatus"       "success" — failed attempts are skipped
The new owner's NAME is not in the item. It is resolved from people the bot
already knows — creators on the groups tab, operators of any audit event — and
back-filled hourly by SheetsClient.fill_owner_names().

The 2026-09-02 note in the data dictionary that the new owner was absent only
looked at recipients / extend / object_detail and missed common_drawers.

Owners who LEFT the organisation are handled by leavers.py
(user_active_exit_team); it sets owner_status = "left" through
SheetsClient.mark_creators_left().

Same window mechanics as deletions.py: 30 days per query, 180 days retention.
"""
from __future__ import annotations
from datetime import datetime, timezone

from deletions import _chat_from_objects, _operator_name, sweep_latest
from newgroups import _drawer_map
from storage.sheets import get_sheets
from utils.logger import get_logger

logger = get_logger(__name__)

TRANSFER_EVENT = "im_forward_chatadmin"
NEW_OWNER_KEY = "im_forward_adminlist"     # drawer key: new owner's user_id
OPSTATUS_KEY = "im_chat_opstatus"          # drawer key: "success" / failure


def parse_owner_transfer(item: dict) -> dict | None:
    """One im_forward_chatadmin item → {chat_id, group_name, event_time,
    transferred_at, transferred_from, transferred_from_id, new_owner_id}.
    None for items without a chat, and for failed attempts."""
    chat_id, name = _chat_from_objects(item)
    if not chat_id:
        return None
    drawers = _drawer_map(item)
    status = str(drawers.get(OPSTATUS_KEY, "")).strip().lower()
    if status and status != "success":
        return None
    try:
        ts = int(item.get("event_time") or 0)
    except (TypeError, ValueError):
        ts = 0
    when = (datetime.fromtimestamp(ts, tz=timezone.utc)
            if ts else datetime.now(timezone.utc))
    return {
        "chat_id": chat_id,
        "group_name": name,
        "event_time": ts,
        "transferred_at": when.isoformat(),
        "transferred_from": _operator_name(item),
        "transferred_from_id": str(item.get("operator_value") or "").strip(),
        "new_owner_id": str(drawers.get(NEW_OWNER_KEY) or "").strip(),
    }


async def collect_owner_transfers(lookback_days: int) -> dict[str, dict]:
    """The latest successful ownership transfer per chat in `lookback_days`."""
    return await sweep_latest(TRANSFER_EVENT, lookback_days, parse_owner_transfer)


async def scan_owner_transfers(lookback_days: int) -> list[str]:
    """Record ownership transfers on the tracked rows: who owns it now (from
    the log), when it moved and who handed it over.

    Returns the chat_ids newly recorded. Idempotent — a row already holding
    this (or a later) transfer is skipped, so re-scanning an overlapping hourly
    window costs no writes.
    """
    found = await collect_owner_transfers(lookback_days)
    if not found:
        return []
    # The outgoing owners are named in these very items — and the new owner of
    # one group is often the old owner of another, so they help name each other.
    people = {rec["transferred_from_id"]: rec["transferred_from"]
              for rec in found.values()
              if rec.get("transferred_from_id") and rec.get("transferred_from")}
    recorded = get_sheets().mark_owner_transferred(found, people)
    for chat_id in recorded:
        rec = found[chat_id]
        logger.info("Owner changed: %r → %s (handed over by %s on %s)",
                    rec["group_name"] or chat_id, rec["new_owner_id"] or "?",
                    rec["transferred_from"] or "?", rec["transferred_at"][:10])
    return recorded
