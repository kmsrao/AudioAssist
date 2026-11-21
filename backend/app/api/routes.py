"""REST API. All endpoints live under ``/api``. Interactive docs: ``/docs``.

None of these endpoints modify audio or control a mixer: "applied" only
records that the *operator* made an adjustment so the analyzer can verify it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from app.config.settings import (ALLOWED_BLOCK_SIZES, ALLOWED_FFT_SIZES, ALLOWED_SAMPLE_RATES, AnalysisSettings)
from app.logging.setup import get_logger
from app.models.schemas import (ChannelsUpdate, DeviceChange, DeviceTestRequest, GroupsUpdate, OperatorNote,
                                SelectionUpdate, SettingsUpdate)
from app.services.assist_service import AssistService
from app.websocket.hub import hub

log = get_logger("api")
router = APIRouter(prefix="/api")


def svc(request: Request) -> AssistService:
    return request.app.state.service


def _bad(exc: Exception, code: int = 400) -> HTTPException:
    log.warning("api error", extra={"error": str(exc), "code": code})
    return HTTPException(status_code=code, detail=str(exc))


# ------------------------------------------------------------------ status / health
@router.get("/status")
def get_status(request: Request) -> dict[str, Any]:
    return svc(request).status()


@router.get("/health")
def get_health(request: Request) -> dict[str, Any]:
    return svc(request).health()


# ------------------------------------------------------------------ devices
@router.get("/devices")
async def list_devices(request: Request, refresh: bool = False) -> dict[str, Any]:
    s = svc(request)
    if refresh and s.source is not None and s.source.kind == "device":
        raise HTTPException(409, "Stop capture before rescanning devices (PortAudio must be re-initialised).")
    try:
        devs = await run_in_threadpool(s.devices.list_input_devices, refresh)
    except Exception as exc:  # noqa: BLE001 - PortAudio missing / broken
        return {"available": False, "error": str(exc), "devices": [], "history": s.repo.device_history()}
    return {"available": True, "devices": [d.to_dict() for d in devs], "history": s.repo.device_history(),
            "options": {"sample_rates": ALLOWED_SAMPLE_RATES, "block_sizes": ALLOWED_BLOCK_SIZES}}


@router.get("/devices/{index}")
async def get_device(request: Request, index: int) -> dict[str, Any]:
    info = await run_in_threadpool(svc(request).devices.get_device, index)
    if info is None:
        raise HTTPException(404, f"input device {index} not found")
    return info.to_dict()


@router.post("/devices/{index}/test")
async def test_device(request: Request, index: int, body: DeviceTestRequest | None = None) -> dict[str, Any]:
    body = body or DeviceTestRequest()
    try:
        return await run_in_threadpool(svc(request).test_device, index, body.channels, body.sample_rate, body.duration_s)
    except ValueError as exc:
        raise _bad(exc, 404) from exc


# ------------------------------------------------------------------ capture
@router.get("/capture")
def capture_status(request: Request) -> dict[str, Any]:
    return svc(request).capture_status()


@router.post("/capture/start")
async def capture_start(request: Request) -> dict[str, Any]:
    try:
        return await run_in_threadpool(svc(request).start_capture)
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc, 409) from exc


@router.post("/capture/stop")
async def capture_stop(request: Request) -> dict[str, Any]:
    return await run_in_threadpool(svc(request).stop_capture)


@router.post("/capture/restart")
async def capture_restart(request: Request) -> dict[str, Any]:
    try:
        return await run_in_threadpool(svc(request).restart_capture)
    except Exception as exc:  # noqa: BLE001
        raise _bad(exc, 409) from exc


@router.post("/capture/device")
async def change_device(request: Request, body: DeviceChange) -> dict[str, Any]:
    try:
        out = await run_in_threadpool(lambda: svc(request).change_device(**body.model_dump()))
        hub.bump_channels()
        return out
    except ValueError as exc:
        raise _bad(exc) from exc
    except Exception as exc:  # noqa: BLE001 - driver refused the stream etc.
        raise _bad(exc, 409) from exc


# ------------------------------------------------------------------ channels
@router.get("/channels")
def get_channels(request: Request) -> dict[str, Any]:
    s = svc(request)
    return {"channels": [c.to_api() for c in s.visible_channels()], "groups": s.groups}


@router.put("/channels")
def put_channels(request: Request, body: ChannelsUpdate) -> dict[str, Any]:
    s = svc(request)
    for c in body.channels:
        if c.group not in s.groups:
            raise HTTPException(400, f"unknown group '{c.group}'")
    chans = s.update_channels(body.channels)
    hub.bump_channels()
    return {"channels": [c.to_api() for c in chans], "groups": s.groups}


@router.get("/channels/selected")
def get_selected(request: Request) -> dict[str, Any]:
    return {"selected": [i + 1 for i in svc(request).selected_indices()]}


@router.put("/channels/selected")
def put_selected(request: Request, body: SelectionUpdate) -> dict[str, Any]:
    s = svc(request)
    if body.group is not None and body.group not in s.groups:
        raise HTTPException(400, f"unknown group '{body.group}'")
    sel = s.update_selection(**body.model_dump())
    hub.bump_channels()
    return {"selected": sel}


@router.post("/channels/{number}/reanalyze")
def reanalyze_channel(request: Request, number: int) -> dict[str, Any]:
    s = svc(request)
    if s.channel(number - 1) is None:
        raise HTTPException(404, f"channel {number} not found")
    return s.reanalyze_channel(number - 1)


@router.get("/channels/{number}/analysis")
def channel_analysis(request: Request, number: int, spectrogram: bool = False) -> dict[str, Any]:
    out = svc(request).channel_analysis(number, include_spectrogram=spectrogram)
    if out is None:
        raise HTTPException(404, "no analysis for this channel (not selected, or capture not running)")
    return out


@router.get("/groups")
def get_groups(request: Request) -> dict[str, Any]:
    return {"groups": svc(request).groups}


@router.put("/groups")
def put_groups(request: Request, body: GroupsUpdate) -> dict[str, Any]:
    try:
        groups = svc(request).update_groups(body.groups)
        hub.bump_channels()
        return {"groups": groups}
    except ValueError as exc:
        raise _bad(exc) from exc


@router.get("/profiles")
def get_profiles(request: Request) -> dict[str, Any]:
    return {"profiles": svc(request).profiles()}


@router.get("/analysis/grid")
def analysis_grid(request: Request) -> dict[str, Any]:
    return svc(request).grid()


# ------------------------------------------------------------------ diagnostics
@router.get("/diagnostics")
def get_diagnostics(request: Request, channel: int | None = None) -> dict[str, Any]:
    return {"active": svc(request).diagnostics_payload(channel)}


@router.get("/diagnostics/history")
def diagnostics_history(request: Request, channel: int | None = None,
                        limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    return {"history": svc(request).repo.diagnostic_history(channel, limit)}


# ------------------------------------------------------------------ recommendations
@router.get("/recommendations")
def get_recommendations(request: Request) -> dict[str, Any]:
    return svc(request).recommendations_payload()


@router.get("/recommendations/history")
def recommendations_history(request: Request, channel: int | None = None, status: str | None = None,
                            limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    return {"history": svc(request).repo.recommendation_history(channel, status, limit)}


@router.get("/recommendations/{rec_id}")
def get_recommendation(request: Request, rec_id: str) -> dict[str, Any]:
    s = svc(request)
    rec = s.recs.get(rec_id)
    if rec is not None:
        return rec.to_dict()
    stored = s.repo.get_recommendation(rec_id)
    if stored is None:
        raise HTTPException(404, "recommendation not found")
    return stored


def _rec_action(request: Request, rec_id: str, fn: str, *args: Any) -> dict[str, Any]:
    s = svc(request)
    try:
        rec = getattr(s, fn)(rec_id, *args)
    except KeyError as exc:
        raise HTTPException(404, "recommendation not found") from exc
    except ValueError as exc:
        raise _bad(exc, 409) from exc
    return rec.to_dict()


@router.post("/recommendations/{rec_id}/applied")
def rec_applied(request: Request, rec_id: str, body: OperatorNote | None = None) -> dict[str, Any]:
    return _rec_action(request, rec_id, "apply_recommendation", (body or OperatorNote()).note)


@router.post("/recommendations/{rec_id}/dismissed")
def rec_dismissed(request: Request, rec_id: str, body: OperatorNote | None = None) -> dict[str, Any]:
    return _rec_action(request, rec_id, "dismiss_recommendation", (body or OperatorNote()).note)


@router.post("/recommendations/{rec_id}/reanalyze")
def rec_reanalyze(request: Request, rec_id: str) -> dict[str, Any]:
    return _rec_action(request, rec_id, "reanalyze_recommendation")


@router.post("/reanalyze")
def reanalyze(request: Request, channel: int | None = None) -> dict[str, Any]:
    s = svc(request)
    targets = [channel] if channel else [i + 1 for i in s.selected_indices()]
    return {"reanalyzed": [s.reanalyze_channel(n - 1) for n in targets]}


# ------------------------------------------------------------------ settings
@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    s = svc(request)
    return {"settings": s.settings.model_dump(), "defaults": AnalysisSettings().model_dump(),
            "options": {"sample_rates": ALLOWED_SAMPLE_RATES, "block_sizes": ALLOWED_BLOCK_SIZES,
                        "fft_sizes": ALLOWED_FFT_SIZES}}


@router.post("/settings")
async def post_settings(request: Request, body: SettingsUpdate) -> dict[str, Any]:
    try:
        new = await run_in_threadpool(svc(request).update_settings, body.values)
        hub.bump_channels()
    except ValueError as exc:
        raise _bad(exc, 422) from exc
    except Exception as exc:  # noqa: BLE001 - e.g. capture restart failed
        raise _bad(exc, 409) from exc
    return {"settings": new.model_dump()}


@router.put("/settings")
async def put_settings(request: Request, body: SettingsUpdate) -> dict[str, Any]:
    return await post_settings(request, body)


@router.post("/settings/reset")
async def reset_settings(request: Request) -> dict[str, Any]:
    new = await run_in_threadpool(svc(request).reset_settings)
    hub.bump_channels()
    return {"settings": new.model_dump()}


@router.get("/operator-actions")
def operator_actions(request: Request, limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    return {"actions": svc(request).repo.actions(limit)}
