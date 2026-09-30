"""Centralized logging infrastructure for PBI Guard."""

from collections import deque
import logging
from pathlib import Path
import sys

LOG_FILE_PATH = Path(__file__).resolve().parent.parent / "pbi-guard.log"
_LOG_BUFFER = deque(maxlen=200)


class InMemoryLogHandler(logging.Handler):
    """Retains recent log lines for UI inspection via /api/logs."""
    def emit(self, record):
        try:
            msg = self.format(record)
            _LOG_BUFFER.append(msg)
        except Exception:
            pass


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("pbi_guard")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. File Logger (pbi-guard.log)
    try:
        fh = logging.FileHandler(str(LOG_FILE_PATH), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except OSError:
        pass

    # 2. Console Logger - only attach if connected to a real interactive terminal
    # (Prevents duplicating logs into pbi-guard.log when sys.stderr is already redirected)
    if sys.stderr and hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    # 3. In-Memory Buffer for Dashboard UI
    mem_handler = InMemoryLogHandler()
    mem_handler.setLevel(logging.DEBUG)
    mem_handler.setFormatter(formatter)
    logger.addHandler(mem_handler)

    logger.propagate = False
    return logger


log = setup_logger()


def get_recent_logs() -> list[str]:
    return list(_LOG_BUFFER)