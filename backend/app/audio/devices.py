"""Audio device discovery and inspection via PortAudio (python-sounddevice).

All direct ``sounddevice`` calls for discovery live here behind a small
backend protocol so tests can inject a fake backend without hardware.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from app.logging.setup import get_logger

log = get_logger("audio.devices")

PROBE_SAMPLE_RATES = (44100, 48000, 88200, 96000)


class DeviceBackend(Protocol):
    def query_devices(self) -> list[dict[str, Any]]: ...
    def query_hostapis(self) -> list[dict[str, Any]]: ...
    def default_input_index(self) -> int | None: ...
    def check_input_settings(self, device: int, channels: int, samplerate: int) -> None: ...
    def rescan(self) -> None: ...


class SoundDeviceBackend:
    """Thin wrapper around python-sounddevice."""

    def __init__(self) -> None:
        import sounddevice as sd  # imported lazily so tests can run without PortAudio

        self.sd = sd

    def query_devices(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self.sd.query_devices()]

    def query_hostapis(self) -> list[dict[str, Any]]:
        return [dict(h) for h in self.sd.query_hostapis()]

    def default_input_index(self) -> int | None:
        try:
            idx = self.sd.default.device[0]
            return int(idx) if idx is not None and idx >= 0 else None
        except Exception:  # noqa: BLE001
            return None

    def check_input_settings(self, device: int, channels: int, samplerate: int) -> None:
        self.sd.check_input_settings(device=device, channels=channels, samplerate=samplerate, dtype="float32")

    def rescan(self) -> None:
        # PortAudio only enumerates devices at initialisation. Re-initialising
        # is only safe when no stream is open; the caller guarantees that.
        self.sd._terminate()
        self.sd._initialize()


@dataclass
class DeviceInfo:
    index: int
    name: str
    hostapi: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    default_low_input_latency: float
    default_high_input_latency: float
    is_default_input: bool = False
    supported_sample_rates: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class DeviceManager:
    def __init__(self, backend: DeviceBackend | None = None):
        self._backend = backend
        self._lock = threading.Lock()
        self._rate_cache: dict[tuple[int, str], list[int]] = {}

    @property
    def backend(self) -> DeviceBackend:
        if self._backend is None:
            self._backend = SoundDeviceBackend()
        return self._backend

    @property
    def available(self) -> bool:
        try:
            _ = self.backend
            return True
        except Exception:  # noqa: BLE001 - PortAudio missing
            return False

    def list_input_devices(self, refresh: bool = False, probe_rates: bool = True) -> list[DeviceInfo]:
        with self._lock:
            if refresh:
                try:
                    self.backend.rescan()
                    self._rate_cache.clear()
                    log.info("device rescan requested")
                except Exception as exc:  # noqa: BLE001
                    log.warning("device rescan failed", extra={"error": str(exc)})
            devices = self.backend.query_devices()
            hostapis = self.backend.query_hostapis()
            default_in = self.backend.default_input_index()
            out: list[DeviceInfo] = []
            for idx, d in enumerate(devices):
                if int(d.get("max_input_channels", 0)) <= 0:
                    continue
                info = self._make_info(idx, d, hostapis, default_in)
                if probe_rates:
                    info.supported_sample_rates = self._probe_rates(info)
                out.append(info)
            log.info("audio devices discovered", extra={"input_devices": len(out)})
            return out

    def get_device(self, index: int, probe_rates: bool = True) -> DeviceInfo | None:
        devices = self.backend.query_devices()
        if index < 0 or index >= len(devices):
            return None
        d = devices[index]
        if int(d.get("max_input_channels", 0)) <= 0:
            return None
        info = self._make_info(index, d, self.backend.query_hostapis(), self.backend.default_input_index())
        if probe_rates:
            info.supported_sample_rates = self._probe_rates(info)
        return info

    def find_by_name(self, name: str, hostapi: str | None = None) -> DeviceInfo | None:
        """Device indices are not stable across reboots / replugging, so a
        saved configuration is matched by name (+ host API) first."""
        for dev in self.list_input_devices(probe_rates=False):
            if dev.name == name and (hostapi is None or dev.hostapi == hostapi):
                return dev
        return None

    def _make_info(self, idx: int, d: dict, hostapis: list[dict], default_in: int | None) -> DeviceInfo:
        ha_idx = int(d.get("hostapi", 0))
        ha_name = hostapis[ha_idx]["name"] if 0 <= ha_idx < len(hostapis) else "unknown"
        return DeviceInfo(
            index=idx,
            name=str(d.get("name", f"Device {idx}")),
            hostapi=ha_name,
            max_input_channels=int(d.get("max_input_channels", 0)),
            max_output_channels=int(d.get("max_output_channels", 0)),
            default_samplerate=float(d.get("default_samplerate", 0.0)),
            default_low_input_latency=float(d.get("default_low_input_latency", 0.0)),
            default_high_input_latency=float(d.get("default_high_input_latency", 0.0)),
            is_default_input=(idx == default_in),
        )

    def _probe_rates(self, info: DeviceInfo) -> list[int]:
        key = (info.index, info.name)
        if key in self._rate_cache:
            return self._rate_cache[key]
        rates: list[int] = []
        for sr in PROBE_SAMPLE_RATES:
            try:
                self.backend.check_input_settings(info.index, min(info.max_input_channels, 2), sr)
                rates.append(sr)
            except Exception:  # noqa: BLE001 - unsupported rate
                continue
        self._rate_cache[key] = rates
        return rates

    def validate_config(self, index: int, channels: int, samplerate: int) -> tuple[bool, str]:
        info = self.get_device(index, probe_rates=False)
        if info is None:
            return False, f"Input device {index} not found"
        if channels > info.max_input_channels:
            return False, f"Device supports at most {info.max_input_channels} input channels"
        try:
            self.backend.check_input_settings(index, channels, samplerate)
        except Exception as exc:  # noqa: BLE001
            return False, f"Configuration rejected by driver: {exc}"
        return True, "ok"


def test_device(index: int, channels: int, samplerate: int, blocksize: int, duration_s: float = 1.0) -> dict[str, Any]:
    """Open a short-lived stream and report per-channel levels.

    Must only be called when the main capture stream is not using the device
    (many drivers - especially ASIO/WDM-KS - allow a single client)."""
    import sounddevice as sd

    duration_s = max(0.2, min(duration_s, 5.0))
    frames = int(samplerate * duration_s)
    started = time.monotonic()
    try:
        rec = sd.rec(frames, samplerate=samplerate, channels=channels, dtype="float32", device=index, blocking=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("device test failed", extra={"device": index, "error": str(exc)})
        return {"ok": False, "error": str(exc)}
    rec = np.nan_to_num(rec)
    peak = np.max(np.abs(rec), axis=0)
    rms = np.sqrt(np.mean(rec.astype(np.float64) ** 2, axis=0))
    to_db = lambda v: float(20 * np.log10(max(float(v), 1e-10)))  # noqa: E731
    result = {
        "ok": True,
        "device": index,
        "sample_rate": samplerate,
        "duration_s": round(time.monotonic() - started, 3),
        "channels": [
            {"channel": i + 1, "peak_dbfs": round(to_db(peak[i]), 1), "rms_dbfs": round(to_db(rms[i]), 1),
             "signal_present": bool(to_db(rms[i]) > -70)}
            for i in range(channels)
        ],
    }
    log.info("device test completed", extra={"device": index, "channels": channels})
    return result
