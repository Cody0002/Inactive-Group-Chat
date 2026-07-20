"""
New-group detection from the admin audit log (`im_create_chat`).

This is the audit-sourced counterpart to the real-time `im.chat.created_v1`
webhook. It carries the full "recommended" detail set — members at creation and
external-users flag — which the webhook event does not expose.

Reused by:
  - the daily check (short lookback, catches anything the webhook missed)
  - the first-run backfill (up to 30-day lookback)

Dedup is handled by SheetsClient.log_new_group (keyed on chat_id), so a group
reported by the webhook won't be re-notified here, and vice versa.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

from config import settings
from lark import api, cards
from storage.sheets import get_sheets
from utils.logger import get_logger

logger = get_logger(__name__)

CREATE_EVENT = "im_create_chat"
MESSENGER_EVENT_MODULE = 2
CHAT_OBJECT_TYPE = "4"
AUDIT_PAGE_SIZE = 200
MEMBERS_KEY = "im_addchat_peopel_number"   # Lark's own (typo'd) key
IM_DRAWER_KEY = "im"


def _drawer_map(item: dict) -> dict[str, str]:
    """Flatten common_drawers.common_draw_info_list to {info_key: info_val}."""
    out: dict[str, str] = {}
    drawers = item.get("common_drawers", {}) or {}
    for info in drawers.get("common_draw_info_list", []) or []:
        key = info.get("info_key")
        if key:
            out[key] = info.get("info_val", "")
    return out


def _im_fields(drawers: dict[str, str]) -> dict[str, str]:
    """Parse the nested JSON 'im' drawer into {key: value}."""
    raw = drawers.get(IM_DRAWER_KEY)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return {f.get("key"): f.get("value", "") for f in parsed if f.get("key")}


def _creator_name(item: dict) -> str:
    detail = (item.get("operator_detail", {}) or {}).get("operator_name", {}) or {}
    i18n = detail.get("i18n_value", {}) or {}
    return i18n.get("en_us") or detail.get("default_name") or ""


def _chat_from_objects(item: dict) -> tuple[str, str]:
    """Return (chat_id, group_name) from the objects[] entry of type chat."""
    for obj in item.get("objects", []) or []:
        if str(obj.get("object_type", "")) == CHAT_OBJECT_TYPE:
            value = obj.get("object_value", "")
            if isinstance(value, str) and value.startswith("oc_"):
                return value, obj.get("object_name", "")
    return "", ""


def parse_created_group(item: dict) -> dict | None:
    """Extract the recommended detail set from one im_create_chat audit item."""
    chat_id, name = _chat_from_objects(item)
    drawers = _drawer_map(item)
    im = _im_fields(drawers)

    if not chat_id:
        chat_id = im.get("chat_id", "")
    if not name:
        name = im.get("chat_name", "")
    if not chat_id:
        return None

    ts = 0
    try:
        ts = int(item.get("event_time") or 0)
    except (TypeError, ValueError):
        ts = 0
    created_dt = (datetime.fromtimestamp(ts, tz=timezone.utc)
                  if ts else datetime.now(timezone.utc))

    return {
        "chat_id": chat_id,
        "group_name": name or "Unnamed group",
        "creator_id": item.get("operator_value", ""),   # audit user_id, not taggable
        "creator_name": _creator_name(item),
        "created_at": created_dt.isoformat(),
        "created_display": created_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "members": drawers.get(MEMBERS_KEY, ""),
        "external": im.get("is_cross_tenant", ""),
    }


async def scan_new_groups(lookback_days: int, *, notify: bool = True) -> int:
    """Detect newly created groups from the audit log, log them, and (optionally)
    post the detail card to the central group. Returns the count of NEW groups."""
    lookback_days = max(1, min(lookback_days, 30))
    now = datetime.now(timezone.utc)
    oldest = int((now - timedelta(days=lookback_days)).timestamp())
    latest = int(now.timestamp())

    sheets = get_sheets()
    logger.info("Scanning new groups (im_create_chat), lookback=%sd", lookback_days)

    # Collect every created-group record first, then write to the sheet in
    # bulk — per-row writes blow the Sheets API quota on a 30-day base build.
    page_token = None
    recs: dict[str, dict] = {}
    while True:
        try:
            data = await api.get_behavior_audit_logs(
                oldest=oldest, latest=latest,
                event_name=CREATE_EVENT, event_module=MESSENGER_EVENT_MODULE,
                page_size=AUDIT_PAGE_SIZE, page_token=page_token,
            )
        except Exception as e:
            logger.warning(f"New-group scan fetch failed: {e}")
            break

        for item in data.get("items", []):
            if item.get("event_name") != CREATE_EVENT:
                continue
            rec = parse_created_group(item)
            if not rec or rec["chat_id"] == settings.CENTRAL_GROUP_CHAT_ID:
                continue
            recs.setdefault(rec["chat_id"], rec)

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break

    added = set(sheets.log_new_groups_bulk(list(recs.values())))

    # Already-known groups (e.g. logged by the webhook without size): fill in
    # member_count / external if they're still blank.
    for chat_id, rec in recs.items():
        if chat_id not in added:
            sheets.set_group_meta(chat_id, rec["members"], rec["external"])

    for chat_id in added:
        rec = recs[chat_id]
        logger.info(f"New group (audit): {rec['group_name']}")
        if notify:
            await api.send_message(
                settings.CENTRAL_GROUP_CHAT_ID,
                cards.new_group_created_card(
                    rec["group_name"], "", rec["creator_name"], rec["chat_id"],
                    created=rec["created_display"],
                    members=rec["members"], external=rec["external"],
                ),
                msg_type="interactive")

    logger.info("New-group scan: %s seen, %s new", len(recs), len(added))
    return len(added)
