"""
Webhook receiver — events + slash commands.

Notification-only bot. No authorization, no OAuth.

Events:
  im.chat.created_v1    → log new group silently (surfaces in the daily digest;
                          only pings if NOTIFY_ON_NEW_GROUP is on)
  im.message.receive_v1 → /daily, /week, /detail, /help
"""
import asyncio
import json
import time

from fastapi import APIRouter, Request, HTTPException

from config import settings
from lark import api, cards
from storage.sheets import get_sheets
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Lark redelivers an event if it doesn't get a 200 quickly, and each delivery
# carries the same event_id. Remember recent ids so a retry never runs the
# handler twice (the "one /daily → three reports" bug).
_seen_events: dict[str, float] = {}
EVENT_DEDUP_TTL = 3600  # seconds


def _is_duplicate(event_id: str) -> bool:
    now = time.time()
    for k, t in list(_seen_events.items()):
        if now - t > EVENT_DEDUP_TTL:
            _seen_events.pop(k, None)
    if not event_id:
        return False
    if event_id in _seen_events:
        return True
    _seen_events[event_id] = now
    return False


async def _run_handler(coro):
    try:
        await coro
    except Exception:
        logger.exception("Event handler failed")


@router.post("/webhook")
async def lark_webhook(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")

    if body.get("type") == "url_verification":
        if body.get("token") != settings.LARK_VERIFICATION_TOKEN:
            raise HTTPException(401, "Invalid token")
        return {"challenge": body["challenge"]}

    header = body.get("header", {})
    if header.get("token") != settings.LARK_VERIFICATION_TOKEN:
        raise HTTPException(401, "Invalid token")

    event_type = header.get("event_type")
    event = body.get("event", {})

    if _is_duplicate(header.get("event_id", "")):
        return {"code": 0}

    # ACK immediately and do the work in the background — if Lark waits on a
    # slow handler it times out and redelivers, producing duplicate reports.
    if event_type == "im.chat.created_v1":
        asyncio.create_task(_run_handler(_handle_new_group(event)))
    elif event_type == "im.message.receive_v1":
        asyncio.create_task(_run_handler(_handle_message(event)))

    return {"code": 0}


# ----------------------------------------------------------------
# New group created — log it and post an informational notice
# ----------------------------------------------------------------
async def _handle_new_group(event: dict):
    chat_id = event.get("chat_id", "")
    name = event.get("name", "Unnamed group")
    creator_id = event.get("owner_id", "")
    if not chat_id or chat_id == settings.CENTRAL_GROUP_CHAT_ID:
        return

    creator_name = creator_id
    if creator_id:
        try:
            info = await api.get_user_info(creator_id)
            creator_name = info.get("name", creator_id)
        except Exception:
            pass

    sheets = get_sheets()
    if not sheets.log_new_group(chat_id, name, creator_id, creator_name):
        return  # duplicate event
    logger.info(f"New group: {name} (by {creator_name})")

    # New groups are surfaced in the daily digest, not pinged individually —
    # unless the operator has opted in to real-time notices.
    if settings.NOTIFY_ON_NEW_GROUP:
        await api.send_message(
            settings.CENTRAL_GROUP_CHAT_ID,
            cards.new_group_created_card(name, creator_id, creator_name, chat_id),
            msg_type="interactive")


# ----------------------------------------------------------------
# Slash commands
# ----------------------------------------------------------------
async def _handle_message(event: dict):
    message = event.get("message", {})
    if message.get("chat_id") != settings.CENTRAL_GROUP_CHAT_ID:
        return
    if event.get("sender", {}).get("sender_type") != "user":
        return

    try:
        text = json.loads(message.get("content", "{}")).get("text", "").strip()
    except json.JSONDecodeError:
        text = ""

    msg_id = message["message_id"]
    # Drop @mention placeholders (e.g. "@_user_1 /help" when the bot is
    # @mentioned) so the command is found regardless of how it was sent.
    words = [w for w in text.split() if not w.startswith("@_user_")]
    cmd = words[0].lower() if words else ""

    if cmd == "/daily":
        await _cmd_daily(msg_id)
    elif cmd == "/week":
        await _cmd_week(msg_id)
    elif cmd == "/detail":
        await _cmd_detail(msg_id)
    elif cmd == "/help":
        await api.reply_in_thread(msg_id, cards.help_card(), msg_type="interactive")


async def _await_fresh_data(parent_msg_id: str) -> None:
    """If a data scan is running, tell the user and wait for it so their
    report uses fresh data instead of failing or showing stale numbers."""
    from checker import refresh_in_progress, wait_for_refresh
    if refresh_in_progress():
        await api.reply_in_thread(
            parent_msg_id,
            cards.loading_card("Report is in progress — I'm refreshing the "
                               "data right now. It will be posted here the "
                               "moment the scan finishes."),
            msg_type="interactive")
        await wait_for_refresh()


async def _cmd_daily(parent_msg_id: str):
    # Renders straight from the sheet (hourly refresh keeps it fresh) — fast,
    # no audit scans. Reply explicitly when there is nothing to show.
    await _await_fresh_data(parent_msg_id)
    from checker import run_daily_check
    sent = await run_daily_check()
    if not sent:
        await api.reply_in_thread(
            parent_msg_id,
            json.dumps({"text": "✅ Nothing to report — no new groups and "
                                "nothing approaching inactive."}),
            msg_type="text")


async def _cmd_week(parent_msg_id: str):
    # Renders straight from the sheet — fast, no audit scans.
    await _await_fresh_data(parent_msg_id)
    from weekly_summary import run_weekly_summary
    sent = await run_weekly_summary()
    if not sent:
        await api.reply_in_thread(
            parent_msg_id,
            json.dumps({"text": "No groups tracked yet — the sheet is empty."}),
            msg_type="text")


async def _cmd_detail(parent_msg_id: str):
    url = (f"https://docs.google.com/spreadsheets/d/"
           f"{settings.SPREADSHEET_ID}/edit")
    await api.reply_in_thread(parent_msg_id, cards.detail_card(url),
                              msg_type="interactive")
