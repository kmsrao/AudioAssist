"""Structured logging with local rotation.

Console gets a human-readable line; ``logs/app.log`` gets one JSON object per
line (rotated at 5 MB, 5 backups). Audio sample content is never logged -
only metadata such as levels, counts and timings.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extras = {k: v for k, v in vars(record).items() if k not in _RESERVED}
        if extras:
            payload["data"] = extras
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname:<7} {record.name}: {record.getMessage()}"
        extras = {k: v for k, v in vars(record).items() if k not in _RESERVED}
        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_configured = False


def configure_logging(logs_dir: Path, level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(ConsoleFormatter())
    root.addHandler(console)

    # uvicorn access logs are noisy at UI refresh rates; keep warnings only.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"laa.{name}")
