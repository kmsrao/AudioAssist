"""Real-time WebSocket feed (``/ws``).

Each client gets its own sender task paced at ``ui_refresh_hz``; the analysis
thread is never blocked by slow clients. Messages are compact JSON:

server -> client
  hello            {grid, channels, groups, profiles, settings}     on connect
  status           app/device/capture/health summary               ~1 Hz
  levels           per-channel rms/peak/clip/activity               every UI tick
  analysis         focused channel: spectrum, bands, peaks, stats   every UI tick (when changed)
  spectrogram      full history for the focused channel             on focus change
  recommendations  {version, active, recent}                        when changed
  diagnostics      active diagnostics (incl. suppressed)            when changed
  channels         channel configuration                            when changed by any client
  error            {message}

client -> server
  {"type": "focus", "channel": 3}         choose channel for spectrum data
  {"type": "rate", "hz": 8}               request a lower/higher UI rate (clamped 1-30)
  {"type": "ping"}
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.logging.setup import get_logger
from app.services.assist_service import AssistService

log = get_logger("websocket")
router = APIRouter()


def _clean(o: Any) -> Any:
    if isinstance(o, float):
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _dump(msg: dict[str, Any]) -> str:
    try:
        return json.dumps(msg, separators=(",", ":"), allow_nan=False, default=str)
    except ValueError:  # NaN/inf slipped through: replace with null rather than kill the feed
        return json.dumps(_clean(msg), separators=(",", ":"), default=str)


class ClientState:
    def __init__(self) -> None:
        self.focus: int | None = None
        self.rate_hz: float | None = None
        self.sent_focus: int | None = None
        self.rec_version = -1
        self.diag_version = -1
        self.chan_version = -1
        self.last_tick = -1
        self.last_status = 0.0
        self.grid_key: Any = None


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.channels_version = 0

    def bump_channels(self) -> None:
        self.channels_version += 1


hub = Hub()


def _hello(s: AssistService) -> dict[str, Any]:
    return {"type": "hello", "grid": s.grid(), "channels": [c.to_api() for c in s.visible_channels()],
            "groups": s.groups, "profiles": s.profiles(), "settings": s.settings.model_dump()}


async def _receiver(ws: WebSocket, st: ClientState) -> None:
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = msg.get("type")
        if kind == "focus":
            ch = msg.get("channel")
            st.focus = int(ch) if ch is not None else None
        elif kind == "rate":
            st.rate_hz = max(1.0, min(30.0, float(msg.get("hz", 10))))
        elif kind == "ping":
            await ws.send_text(_dump({"type": "pong", "t": time.time()}))


async def _sender(ws: WebSocket, s: AssistService, st: ClientState) -> None:
    await ws.send_text(_dump(_hello(s)))
    st.grid_key = (s.analyzer.nfft, s.analyzer.fs) if s.analyzer else None
    st.chan_version = hub.channels_version
    while True:
        t0 = time.monotonic()
        rate = st.rate_hz or s.settings.ui_refresh_hz
        grid_key = (s.analyzer.nfft, s.analyzer.fs, len(s.analyzer.grid_display.centers)) if s.analyzer else None
        if grid_key != st.grid_key or st.chan_version != hub.channels_version:
            st.grid_key, st.chan_version = grid_key, hub.channels_version
            await ws.send_text(_dump(_hello(s)))
            st.sent_focus = None
        if t0 - st.last_status >= 1.0:
            st.last_status = t0
            await ws.send_text(_dump({"type": "status", **s.status()}))
        await ws.send_text(_dump({"type": "levels", "tick": s.ticks, "levels": s.levels}))
        if st.focus is not None:
            full = st.sent_focus != st.focus
            if full or s.ticks != st.last_tick:
                data = s.channel_analysis(st.focus, include_spectrogram=full)
                if data is not None:
                    await ws.send_text(_dump({"type": "analysis", "full": full, **data}))
                    st.sent_focus = st.focus
            st.last_tick = s.ticks
        if s.recs.version != st.rec_version:
            st.rec_version = s.recs.version
            await ws.send_text(_dump({"type": "recommendations", **s.recommendations_payload()}))
        if s.diag_version != st.diag_version:
            st.diag_version = s.diag_version
            await ws.send_text(_dump({"type": "diagnostics", "active": s.diagnostics_payload()}))
        elapsed = time.monotonic() - t0
        await asyncio.sleep(max(0.01, 1.0 / rate - elapsed))


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    s: AssistService = ws.app.state.service
    await ws.accept()
    hub.clients.add(ws)
    client = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    log.info("websocket connected", extra={"client": client, "clients": len(hub.clients)})
    st = ClientState()
    sel = s.selected_indices()
    st.focus = sel[0] + 1 if sel else None
    recv = asyncio.create_task(_receiver(ws, st))
    send = asyncio.create_task(_sender(ws, s, st))
    try:
        done, pending = await asyncio.wait({recv, send}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.warning("websocket task ended with error", extra={"client": client, "error": repr(exc)})
    finally:
        hub.clients.discard(ws)
        log.info("websocket disconnected", extra={"client": client, "clients": len(hub.clients)})
