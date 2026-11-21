"""OS-specific process tuning for steady real-time analysis.

Windows 11 on hybrid CPUs (performance + efficiency cores) applies "EcoQoS"
power throttling to processes that look bursty/background. A thread that does
~20 ms of work every 100 ms and then sleeps is a typical victim: it gets
scheduled on efficiency cores at reduced clock and runs 3-4x slower (measured
during development: 64 ms -> 17 ms average analysis tick for 8 channels).

We opt this process out of execution-speed throttling and request 1 ms timer
resolution so ``sleep``/``Event.wait`` pacing is accurate. Both are documented,
reversible per-process settings; nothing system-wide is changed permanently.

macOS: terminal-launched processes are not subject to App Nap, so nothing is
needed there. Linux is not a target platform but is harmless.
"""
from __future__ import annotations

import sys

from app.logging.setup import get_logger

log = get_logger("platform")

_applied = False


def tune_process_for_realtime() -> dict[str, bool]:
    global _applied
    result = {"power_throttling_disabled": False, "timer_resolution_1ms": False}
    if _applied or sys.platform != "win32":
        return result
    _applied = True
    try:
        import ctypes
        from ctypes import wintypes

        class _PowerThrottlingState(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetProcessInformation.restype = wintypes.BOOL
        # ProcessPowerThrottling = 4; PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1; StateMask 0 = never throttle
        state = _PowerThrottlingState(1, 0x1, 0x0)
        result["power_throttling_disabled"] = bool(kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state)))
    except Exception as exc:  # noqa: BLE001 - older Windows versions
        log.warning("could not disable power throttling", extra={"error": str(exc)})
    try:
        import ctypes

        result["timer_resolution_1ms"] = ctypes.WinDLL("winmm").timeBeginPeriod(1) == 0
    except Exception as exc:  # noqa: BLE001
        log.warning("could not set timer resolution", extra={"error": str(exc)})
    log.info("windows process tuning", extra=result)
    return result
