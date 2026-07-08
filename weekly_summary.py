"""
Friday weekly summary — activity digest from the sheet (audit-driven data).
Also prunes old alert-log rows to keep the sheet small.

Notification-only: no authorization, no admin tokens, no coverage. It simply
reports the current state of every monitored group.
"""
from config import settings
from lark import api, cards
from storage.sheets import get_sheets
from utils.logger import get_logger

logger = get_logger(__name__)


async def run_weekly_summary() -> bool:
    """Render the weekly summary from the sheet (data kept fresh by the hourly
    scan). Returns True if a report was sent."""
    logger.info("=== Weekly summary starting ===")
    sheets = get_sheets()
    groups = sheets.get_all_groups()

    if groups:
        await api.send_message(
            settings.CENTRAL_GROUP_CHAT_ID,
            cards.weekly_summary_card(groups, settings.INACTIVITY_THRESHOLD_DAYS,
                                      settings.PERSONAL_GROUP_MAX_MEMBERS),
            msg_type="interactive")

    # Housekeeping: prune old alert rows so the sheet stays lean
    try:
        sheets.prune_old_alerts(keep_days=30)
    except Exception as e:
        logger.warning(f"Alert prune failed: {e}")

    inactive = sum(1 for g in groups if g.get("state") == "inactive")
    logger.info(f"=== Summary done — {len(groups)} group(s), {inactive} inactive ===")
    return bool(groups)
