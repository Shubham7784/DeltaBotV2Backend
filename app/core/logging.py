"""Structured logging setup for Delta Bot V2."""

import logging
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any
from collections import deque

# Fixed-size in-memory log buffer for monitoring / frontend log stream
LOG_BUFFER_MAX_LEN = 200
_recent_logs: deque = deque(maxlen=LOG_BUFFER_MAX_LEN)


class MemoryLogHandler(logging.Handler):
    """Custom logging handler to retain recent logs in memory for API inspection."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "line": record.lineno,
            }
            _recent_logs.append(log_entry)
        except Exception:
            self.handleError(record)


def setup_logging(debug: bool = True) -> logging.Logger:
    """Configures root logger with console stream and in-memory log handler."""
    level = logging.DEBUG if debug else logging.INFO

    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            MemoryLogHandler(),
        ],
        force=True,
    )

    logger = logging.getLogger("delta_bot_v2")
    logger.setLevel(level)
    logger.info("Delta Bot V2 logging initialized (level=%s)", logging.getLevelName(level))
    return logger


def get_recent_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Returns the most recent in-memory log entries."""
    items = list(_recent_logs)
    return items[-limit:]
