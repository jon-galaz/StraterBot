"""Logging is the post-mortem lifeline — verify the file sink actually writes."""
from loguru import logger

from trading_system.logging_setup import configure_logging, log_file_path, read_tail


def test_configure_logging_writes_file_and_tail_reads_it(tmp_path):
    url = f"sqlite:///{tmp_path}/trading.db"
    path = configure_logging(url)
    assert path == log_file_path(url)

    logger.info("UNIQUE_MARKER_42")
    logger.remove()  # flush + close the enqueued file sink so the line is on disk

    assert path.exists()
    out = read_tail(path, 200)
    assert "UNIQUE_MARKER_42" in out


def test_log_file_lives_next_to_db(tmp_path):
    url = f"sqlite:///{tmp_path}/data/trading.db"
    assert log_file_path(url) == tmp_path / "data" / "strater.log"
