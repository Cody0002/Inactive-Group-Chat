"""
Data refresh + daily digest — audit-log only.

No authorization, no per-admin tokens, no reading message content.

Split of responsibilities:
  refresh_data()         — the ONLY place that pulls from the audit API.
                           Detects new groups, recomputes every group's activity
                           and state, writes changes to the sheet, and fires
                           inactive alerts (cooldown-guarded).
  run_initial_backfill() — first run on an empty sheet: 30-day base build.
  run_hourly_refresh()   — scheduled every hour so the sheet stays fresh.
  run_daily_check()      — renders the daily digest straight from the sheet;
                           no API scans (data is already there).

Activity for a group is the newest of:
  - the activity already stored in the sheet (last_activity_at)
  - any qualifying audit event mapped to the group's chat_id
  - the group's created_at (fallback)
"""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import settings
from lark import api, cards
from storage.sheets import get_sheets
from utils.logger import get_logger

logger = get_logger(__name__)

MESSENGER_EVENT_MODULE = 2
CHAT_OBJECT_TYPE = "4"
AUDIT_PAGE_SIZE = 200
HOURLY_SCAN_LOOKBACK = 1      # days; hourly refresh window (24x overlap = safe)
BASE_BACKFILL_DAYS = 30       # first-run base: max audit query window

# Member add/remove events → member_count delta per person.
# im_addtochat / im_deletefromchat may cover several people in one event
# (count parsed from the drawer); join/quit are always one person.
MEMBER_EVENTS = {
    "im_addtochat": +1,
    "im_join_chat": +1,
    "im_deletefromchat": -1,
    "im_quit_chat": -1,
}


def _state_for(days: int) -> str:
    if days >= settings.INACTIVITY_THRESHOLD_DAYS:
        return "inactive"
    if days >= settings.WARN_THRESHOLD_DAYS:
        return "warning"
    return "active"


def _parse(iso: str, default: datetime) -> datetime:
    if not iso:
        return default
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return default


# Held while a data refresh runs. Commands can check it to tell the user a
# scan is in progress and wait for fresh data instead of failing silently.
_refresh_lock = asyncio.Lock()


def refresh_in_progress() -> bool:
    return _refresh_lock.locked()


async def wait_for_refresh():
    """Block until the currently running refresh (if any) finishes."""
    async with _refresh_lock:
        pass


async def refresh_data(activity_lookback_days: int | None = None,
                       newgroup_lookback_days: int = HOURLY_SCAN_LOOKBACK,
                       notify_new: bool | None = None):
    """Pull audit data and bring the sheet fully up to date.

    The single place that talks to the audit API. Reports (/daily, /week,
    scheduled digests) render straight from the sheet afterwards.
    """
    async with _refresh_lock:
        await _refresh_data_locked(activity_lookback_days,
                                   newgroup_lookback_days, notify_new)


async def _refresh_data_locked(activity_lookback_days: int | None,
                               newgroup_lookback_days: int,
                               notify_new: bool | None):
    sheets = get_sheets()

    # Detect new groups from the audit log and log them (silently by default;
    # they surface in the daily digest unless NOTIFY_ON_NEW_GROUP is on).
    if notify_new is None:
        notify_new = settings.NOTIFY_ON_NEW_GROUP
    try:
        from newgroups import scan_new_groups
        await scan_new_groups(newgroup_lookback_days, notify=notify_new)
    except Exception as e:
        logger.warning(f"New-group scan failed: {e}")

    # Keep member_count close to reality (adds/joins/removals/quits).
    try:
        await _sync_member_counts(sheets, datetime.now(timezone.utc))
    except Exception as e:
        logger.warning(f"Member-count sync failed: {e}")

    groups = sheets.get_all_groups()
    logger.info(f"Refreshing {len(groups)} group(s)")

    now = datetime.now(timezone.utc)
    audit_activity = await _audit_activity_by_chat(now, activity_lookback_days)
    alerts = 0
    changes: dict[str, dict[str, str]] = {}

    for group in groups:
        chat_id = group.get("chat_id")
        if not chat_id:
            continue

        # Start from the newest activity we already know about (persisted from
        # earlier runs), falling back to the group's creation time.
        last_dt = _parse(group.get("created_at", ""), now)
        activity_source = "created_at_fallback"
        activity_event = ""

        stored = _parse(group.get("last_activity_at", ""), last_dt)
        if stored > last_dt:
            last_dt = stored
            activity_source = group.get("last_activity_source") or "previous"
            activity_event = group.get("last_activity_event_name", "")

        audit_hit = audit_activity.get(chat_id)
        if audit_hit and audit_hit["dt"] > last_dt:
            last_dt = audit_hit["dt"]
            activity_source = "audit_log"
            activity_event = audit_hit["event_name"]

        days = (now - last_dt).days
        state = _state_for(days)

        # Every group gets last_checked_at stamped; rows whose data actually
        # changed get the full field set. All of it goes out in ONE batch
        # call, so this stays inside the Sheets write quota.
        if (group.get("last_activity_at") != last_dt.isoformat()
                or str(group.get("days_inactive")) != str(days)
                or group.get("state") != state):
            changes[chat_id] = {
                "last_activity_at": last_dt.isoformat(),
                "days_inactive": str(days),
                "state": state,
                "last_activity_source": activity_source,
                "last_activity_event_name": activity_event,
            }
        else:
            changes[chat_id] = {}  # timestamp-only: bulk write adds last_checked_at

        if state == "inactive" and not sheets.was_recently_alerted(
                chat_id, "inactive", settings.REALERT_COOLDOWN_DAYS):
            await _alert(group, days, last_dt.strftime("%Y-%m-%d"))
            sheets.log_alert("", chat_id, group.get("group_name", ""), "inactive")
            alerts += 1

    if changes:
        sheets.bulk_update_group_activity(changes)
    data_changed = sum(1 for f in changes.values() if f)
    logger.info(f"Refresh done — {len(changes)} row(s) checked, "
                f"{data_changed} with data changes, {alerts} alert(s)")


async def run_initial_backfill():
    """First run: build the 30-day base (every group created in the window —
    the audit API's maximum — plus its activity and member changes) so reports
    render instantly from the sheet. Guarded by a meta flag so it runs once."""
    sheets = get_sheets()
    if sheets.meta_get("base_built"):
        logger.info("Base already built — normal refresh on startup")
        await refresh_data()
        return
    logger.info(f"=== Building {BASE_BACKFILL_DAYS}-day base from the audit log ===")
    await refresh_data(activity_lookback_days=BASE_BACKFILL_DAYS,
                       newgroup_lookback_days=BASE_BACKFILL_DAYS,
                       notify_new=False)
    sheets.meta_set("base_built", datetime.now(timezone.utc).isoformat())
    logger.info("=== Base build complete ===")


async def _sync_member_counts(sheets, now: datetime):
    """Apply member add/remove audit events to member_count.

    A watermark in the meta tab marks how far we've already processed, so
    overlapping hourly scan windows never double-count. First run covers the
    full 30-day base window."""
    watermark = sheets.meta_get("member_watermark")
    if watermark:
        oldest = int(float(watermark)) + 1
    else:
        oldest = int((now - timedelta(days=BASE_BACKFILL_DAYS)).timestamp())
    latest = int(now.timestamp())
    if latest <= oldest:
        return

    deltas: dict[str, int] = {}
    for event_name, sign in MEMBER_EVENTS.items():
        page_token = None
        while True:
            try:
                data = await api.get_behavior_audit_logs(
                    oldest=oldest, latest=latest, event_name=event_name,
                    event_module=MESSENGER_EVENT_MODULE,
                    page_size=AUDIT_PAGE_SIZE, page_token=page_token)
            except Exception as e:
                logger.warning(f"Member event fetch failed for {event_name}: {e}")
                break
            for item in data.get("items", []):
                count = _member_event_count(item)
                for chat_id in _chat_ids_from_audit_item(item):
                    deltas[chat_id] = deltas.get(chat_id, 0) + sign * count
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break

    changed = sheets.adjust_member_counts(deltas) if deltas else 0
    sheets.meta_set("member_watermark", str(latest))
    if deltas:
        logger.info(f"Member sync: {len(deltas)} chat(s) with changes, "
                    f"{changed} tracked row(s) updated")


def _member_event_count(item: dict) -> int:
    """People covered by one add/remove event (drawer count, default 1)."""
    from newgroups import _drawer_map
    for key, val in _drawer_map(item).items():
        k = key.lower()
        if ("number" in k or "peopel" in k or "people" in k) and str(val).isdigit():
            return max(int(val), 1)
    return 1


async def run_hourly_refresh():
    logger.info("=== Hourly refresh starting ===")
    await refresh_data()


async def run_daily_check() -> bool:
    """Render the daily digest from the sheet. No API scans — the hourly
    refresh keeps the data fresh, so this only shows what's already there.
    Returns True if a digest was sent (False = nothing to report)."""
    logger.info("=== Daily digest (render-only) ===")
    return await _send_daily_digest(get_sheets(), datetime.now(timezone.utc))


def _days(group: dict) -> int:
    try:
        return int(group.get("days_inactive") or 0)
    except (TypeError, ValueError):
        return 0


def _leak_risk(group: dict) -> int:
    """Higher = higher data-leak risk when abandoned: external members weigh
    most, larger (Team) groups next."""
    score = 0
    if str(group.get("external", "")).lower() in ("yes", "true"):
        score += 2
    try:
        if int(group.get("member_count") or 0) > settings.PERSONAL_GROUP_MAX_MEMBERS:
            score += 1
    except (TypeError, ValueError):
        pass
    return score


def _today_window(now: datetime) -> tuple[datetime, datetime, str]:
    """Return (start, end, label) for 'today so far' in the configured local
    timezone: local midnight → right now. So the scheduled noon report covers
    00:00-12:00 and a /daily typed at 19:00 covers 00:00-19:00."""
    tz = ZoneInfo(settings.DAILY_REPORT_TZ)
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    label = (f"Today {start.strftime('%b %d')}, 00:00 → "
             f"{local.strftime('%H:%M')} ({settings.DAILY_REPORT_TZ})")
    return start, local, label


async def _send_daily_digest(sheets, now: datetime) -> bool:
    groups = sheets.get_all_groups()
    start, end, label = _today_window(now)

    new_groups = []
    for g in groups:
        created = _parse(g.get("created_at", ""), None)
        if created and start <= created <= end:
            new_groups.append(g)
    # Latest first
    new_groups.sort(key=lambda g: g.get("created_at", ""), reverse=True)

    threshold = settings.INACTIVITY_THRESHOLD_DAYS
    near_floor = threshold - settings.NEAR_INACTIVE_DAYS
    near = [g for g in groups if _days(g) >= near_floor]
    # Cleanup priority: past-threshold first, then higher leak risk
    # (external / larger groups), then more days quiet.
    near.sort(key=lambda g: (_days(g) >= threshold, _leak_risk(g), _days(g)),
              reverse=True)

    if not new_groups and not near:
        logger.info("Daily digest: nothing to report — no message sent")
        return False

    await api.send_message(
        settings.CENTRAL_GROUP_CHAT_ID,
        cards.daily_digest_card(new_groups, near, threshold, label,
                                settings.PERSONAL_GROUP_MAX_MEMBERS),
        msg_type="interactive")
    logger.info("Daily digest sent: %s new, %s approaching/inactive",
                len(new_groups), len(near))
    return True


async def _audit_activity_by_chat(now: datetime,
                                  lookback_days: int | None = None) -> dict[str, dict]:
    if not settings.AUDIT_ACTIVITY_ENABLED:
        return {}

    events = _audit_activity_events()
    if not events:
        return {}

    if lookback_days is None:
        lookback_days = settings.AUDIT_ACTIVITY_LOOKBACK_DAYS
    lookback_days = max(1, min(lookback_days, 30))
    oldest = int((now - timedelta(days=lookback_days)).timestamp())
    latest = int(now.timestamp())
    latest_by_chat: dict[str, dict] = {}

    logger.info(
        "Loading audit activity for %s event(s), lookback=%sd",
        len(events),
        lookback_days,
    )
    for event_name in events:
        page_token = None
        pages = 0
        count = 0
        while True:
            try:
                data = await api.get_behavior_audit_logs(
                    oldest=oldest,
                    latest=latest,
                    event_name=event_name,
                    event_module=MESSENGER_EVENT_MODULE,
                    page_size=AUDIT_PAGE_SIZE,
                    page_token=page_token,
                )
            except Exception as e:
                logger.warning(f"Audit fetch failed for {event_name}: {e}")
                break

            pages += 1
            items = data.get("items", [])
            count += len(items)
            for item in items:
                event_time = _audit_event_time(item)
                if not event_time:
                    continue
                for chat_id in _chat_ids_from_audit_item(item):
                    current = latest_by_chat.get(chat_id)
                    if not current or event_time > current["event_time"]:
                        latest_by_chat[chat_id] = {
                            "event_time": event_time,
                            "dt": datetime.fromtimestamp(event_time, tz=timezone.utc),
                            "event_name": item.get("event_name", event_name),
                        }

            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            if not page_token:
                break

        logger.info(
            "Audit event %s: %s row(s), %s page(s)",
            event_name,
            count,
            pages,
        )

    logger.info("Audit activity mapped to %s chat(s)", len(latest_by_chat))
    return latest_by_chat


def _audit_activity_events() -> list[str]:
    return [
        event.strip()
        for event in settings.AUDIT_ACTIVITY_EVENTS.split(",")
        if event.strip()
    ]


def _audit_event_time(item: dict) -> int | None:
    try:
        return int(item.get("event_time") or 0) or None
    except (TypeError, ValueError):
        return None


def _chat_ids_from_audit_item(item: dict) -> set[str]:
    chat_ids = set()
    for obj in item.get("objects", []) or []:
        if str(obj.get("object_type", "")) == CHAT_OBJECT_TYPE:
            value = obj.get("object_value")
            if _is_chat_id(value):
                chat_ids.add(value)
    if chat_ids:
        return chat_ids

    return {
        value
        for value in _walk_strings(item)
        if _is_chat_id(value)
    }


def _walk_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found = []
        for child in value.values():
            found.extend(_walk_strings(child))
        return found
    if isinstance(value, list):
        found = []
        for child in value:
            found.extend(_walk_strings(child))
        return found
    return []


def _is_chat_id(value) -> bool:
    return isinstance(value, str) and value.startswith("oc_")


async def _alert(group: dict, days: int, last_active: str):
    """Post the inactive alert to the central group — no @mention."""
    await api.send_message(
        settings.CENTRAL_GROUP_CHAT_ID,
        cards.inactive_alert_card(group.get("group_name", ""), days, last_active),
        msg_type="interactive")
