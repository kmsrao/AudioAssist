from __future__ import annotations

import time
from datetime import datetime, timezone


def now() -> float:
    """Wall-clock seconds since epoch (used for timestamps shown to users)."""
    return time.time()


def mono() -> float:
    """Monotonic seconds (used for durations / persistence windows)."""
    return time.monotonic()


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
