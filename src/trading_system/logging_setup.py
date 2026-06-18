"""
Logging configuration. Writes a rotating log file alongside the DB (so it lives
in the persistent data volume and survives restarts) in addition to stderr.

The file sink is what makes post-mortem analysis possible — pull it via the
Telegram /logs command, or read it off the VM at log_file_path().
"""
import sys
from pathlib import Path

from loguru import logger

from trading_system.sizing import sizing_path


def log_file_path(database_url: str) -> Path:
    """Log file path — same directory as the DB / sizing.json."""
    return sizing_path(database_url).parent / "strater.log"


def configure_logging(database_url: str) -> Path:
    """Route loguru to stderr (INFO) + a rotating file (DEBUG). Idempotent."""
    path = log_file_path(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(
        str(path),
        level="DEBUG",
        rotation="10 MB",      # roll over at 10 MB
        retention="14 days",   # keep two weeks of history
        compression="zip",     # old segments compressed
        enqueue=True,          # thread/async-safe (we log from to_thread workers)
        backtrace=True,        # full tracebacks on exceptions
        diagnose=False,        # but DON'T dump variable values (may hold secrets)
    )
    logger.info(f"Logging to {path} (rotation=10MB, retention=14d)")
    return path


def read_tail(path: Path, n: int) -> str:
    """Return the last `n` lines of the log file (cheap for ≤10 MB segments)."""
    with path.open("r", errors="replace") as f:
        return "".join(f.readlines()[-n:])
