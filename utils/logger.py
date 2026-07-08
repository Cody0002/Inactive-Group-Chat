"""
Logger with automatic 3-day rotation.

Writes to both stdout (for `docker logs` / systemd journal) and a rotating
file. Old logs are deleted automatically — only the last 3 days are kept,
which matters on a small server with limited disk.
"""
import logging
import sys
import os
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_RETENTION_DAYS = 3

_configured = False


def _setup_root():
    global _configured
    if _configured:
        return
    os.makedirs(LOG_DIR, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console handler (stdout)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Rotating file handler — new file at midnight, keep only 3 days
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(LOG_DIR, "bot.log"),
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,   # auto-deletes older than 3 days
        encoding="utf-8",
        utc=True,
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Quiet noisy libraries on a small server
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("gspread").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    _setup_root()
    return logging.getLogger(name)
