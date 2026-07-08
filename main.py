"""
New Group Monitor Bot — entry point.

Optimized for small servers:
  - single worker, single event loop
  - shared HTTP client with connection pooling
  - cached Sheets reads, batched writes
  - 3-day rotating logs

Run: python main.py
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import settings
from lark import webhook
from checker import run_daily_check, run_hourly_refresh, run_initial_backfill
from weekly_summary import run_weekly_summary
from storage.sheets import get_sheets
from utils.http import close_http_client
from utils.logger import get_logger

logger = get_logger(__name__)
scheduler: AsyncIOScheduler | None = None
_startup_task: asyncio.Task | None = None


def _log_task_failure(task: asyncio.Task):
    if not task.cancelled() and task.exception():
        logger.error("Startup refresh failed", exc_info=task.exception())


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler, _startup_task
    logger.info("Starting New Group Monitor Bot…")
    get_sheets()  # init + create tabs

    scheduler = AsyncIOScheduler(timezone="UTC")
    # Hourly data scan keeps the sheet fresh; reports render from the sheet.
    scheduler.add_job(run_hourly_refresh, IntervalTrigger(hours=1),
                      id="hourly_refresh", replace_existing=True,
                      misfire_grace_time=900, coalesce=True, max_instances=1)
    scheduler.add_job(run_daily_check,
                      CronTrigger(hour=settings.DAILY_REPORT_HOUR, minute=0,
                                  timezone=settings.DAILY_REPORT_TZ),
                      id="daily_check", replace_existing=True, misfire_grace_time=3600,
                      coalesce=True, max_instances=1)
    scheduler.add_job(run_weekly_summary,
                      CronTrigger(day_of_week="fri", hour=17, minute=0,
                                  timezone=settings.DAILY_REPORT_TZ),
                      id="weekly_summary", replace_existing=True, misfire_grace_time=3600,
                      coalesce=True, max_instances=1)
    scheduler.start()
    logger.info(f"Scheduled: hourly refresh, daily {settings.DAILY_REPORT_HOUR}:00 "
                f"and weekly Fri 17:00 {settings.DAILY_REPORT_TZ}")

    # 30-day base build on first run (empty sheet); normal refresh otherwise.
    # Runs in the background so the webhook starts serving immediately.
    _startup_task = asyncio.create_task(run_initial_backfill())
    _startup_task.add_done_callback(_log_task_failure)
    yield
    logger.info("Shutting down…")
    if _startup_task and not _startup_task.done():
        _startup_task.cancel()
    if scheduler:
        scheduler.shutdown(wait=False)
    await close_http_client()


app = FastAPI(title="New Group Monitor Bot", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(webhook.router)


@app.get("/health")
async def health():
    s = get_sheets()
    return {"status": "healthy",
            "groups": len(s.get_all_groups())}


if __name__ == "__main__":
    import uvicorn
    # Single worker keeps memory low and avoids duplicate schedulers
    uvicorn.run("main:app", host="0.0.0.0", port=8000, workers=1,
                reload=False, log_level="warning", access_log=False)
