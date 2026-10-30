"""Logging: readable lines on the console, JSON lines in data/logs/pipeline.log (for log shippers)."""
from __future__ import annotations

import json
import logging
import logging.handlers

from cdp.config import LOG_DIR


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                   "logger": record.name, "message": record.getMessage()}
        for key in ("run_id", "tenant_id", "source"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("cdp")
    if root.handlers:
        return
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(LOG_DIR / "pipeline.log", maxBytes=10_000_000,
                                                            backupCount=5, encoding="utf-8")
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)
    except OSError:
        root.warning("Could not open log file in %s; logging to console only", LOG_DIR)
