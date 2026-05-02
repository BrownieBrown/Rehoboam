"""Central logging configuration for rehoboam.

Both the CLI and any background entry points (Azure Functions, daemon)
call `setup_logging()` once at startup. Without this, decisions made
during unattended runs leave no audit trail beyond what `compact_display`
prints to stdout — and stdout disappears when the run ends.

Two handlers:
  - StreamHandler  → stderr at INFO (DEBUG when --verbose). Flows into
    Azure Application Insights for live observation.
  - RotatingFileHandler → logs/rehoboam.log at DEBUG always. Survives
    locally for forensic replay even if upstream log retention drops it.
    5 MiB × 5 backups = ~25 MiB ceiling, which the bot's 2x/day cadence
    fills over roughly several weeks.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_DIR = Path("logs")
_LOG_FILENAME = "rehoboam.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(
    *,
    verbose: bool = False,
    log_dir: Path | None = None,
) -> None:
    """Configure the root logger. Idempotent — safe to call multiple times.

    The first call wins; later calls only adjust the console handler's level
    so toggling `--verbose` mid-process is cheap.
    """
    global _configured

    log_dir = log_dir or _DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / _LOG_FILENAME

    root = logging.getLogger()
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    console_level = logging.DEBUG if verbose else logging.INFO

    if _configured:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
                h.setLevel(console_level)
        return

    root.setLevel(logging.DEBUG)

    stream = logging.StreamHandler(stream=sys.stderr)
    stream.setLevel(console_level)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Quiet noisy third-party loggers — they bury our own decision logs.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    _configured = True
    logging.getLogger(__name__).debug(
        "Logging initialized (console=%s, file=%s)",
        logging.getLevelName(console_level),
        log_path,
    )
