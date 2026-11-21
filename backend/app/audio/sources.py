"""Audio capture sources.

``SoundDeviceSource`` captures from a real interface through PortAudio.
It writes into a ``MultichannelRingBuffer`` and exposes
``CaptureStats``. The real-time rule for the PortAudio callback: copy the
block into the ring buffer, update a few counters, return. No FFT, no I/O, no
logging, no allocation-heavy work.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.audio.ring_buffer import MultichannelRingBuffer
from app.logging.setup import get_logger

log = get_logger("audio.capture")

RING_SECONDS = 4.0


@dataclass
class CaptureStats:
    callbacks: int = 0
    frames: int = 0
    input_overflows: int = 0
    input_underflows: int = 0
    other_status_flags: int = 0
    last_callback_mono: float = 0.0
    max_callback_ms: float = 0.0
    started_mono: float = 0.0
    errors: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        elapsed = max(now - self.started_mono, 1e-6) if self.started_mono else 0.0
        return {
            "callbacks": self.callbacks,
            "frames": self.frames,
            "input_overflows": self.input_overflows,
            "input_underflows": self.input_underflows,
            "other_status_flags": self.other_status_flags,
            "last_callback_age_s": round(now - self.last_callback_mono, 3) if self.last_callback_mono else None,
            "max_callback_ms": round(self.max_callback_ms, 3),
            "callbacks_per_s": round(self.callbacks / elapsed, 1) if elapsed else 0.0,
            "uptime_s": round(elapsed, 1),
            "errors": self.errors[-5:],
        }


class AudioSource:
    kind = "base"

    def __init__(self, sample_rate: int, block_size: int, channels: int):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.channels = channels
        self.ring = MultichannelRingBuffer(int(sample_rate * RING_SECONDS), channels)
        self.stats = CaptureStats()
        self.error: str | None = None
        self.finished = threading.Event()  # set when the stream ends unexpectedly

    @property
    def running(self) -> bool:
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "sample_rate": self.sample_rate, "block_size": self.block_size,
                "channels": self.channels}


class SoundDeviceSource(AudioSource):
    kind = "device"

    def __init__(self, device_index: int, sample_rate: int, block_size: int, channels: int,
                 device_name: str = "", hostapi: str = ""):
        super().__init__(sample_rate, block_size, channels)
        self.device_index = device_index
        self.device_name = device_name
        self.hostapi = hostapi
        self._stream = None

    # ---- real-time callback: keep it tiny ----------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        t0 = time.perf_counter()
        st = self.stats
        if status:
            if status.input_overflow:
                st.input_overflows += 1
            elif status.input_underflow:
                st.input_underflows += 1
            else:
                st.other_status_flags += 1
        self.ring.write(indata)
        st.callbacks += 1
        st.frames += frames
        st.last_callback_mono = time.monotonic()
        dt = (time.perf_counter() - t0) * 1000.0
        if dt > st.max_callback_ms:
            st.max_callback_ms = dt

    def _finished(self) -> None:
        # Called by PortAudio when the stream stops (also on device loss).
        self.finished.set()

    @property
    def running(self) -> bool:
        s = self._stream
        return bool(s is not None and s.active)

    def start(self) -> None:
        import sounddevice as sd

        extra = None
        if "WASAPI" in (self.hostapi or ""):
            # Shared-mode WASAPI resamples if the engine rate differs from the
            # device mix format; without auto_convert PortAudio refuses.
            try:
                extra = sd.WasapiSettings(auto_convert=True)
            except Exception:  # noqa: BLE001 - older sounddevice
                extra = None
        self.stats = CaptureStats(started_mono=time.monotonic())
        self.finished.clear()
        self._stream = sd.InputStream(
            device=self.device_index, channels=self.channels, samplerate=self.sample_rate,
            blocksize=self.block_size, dtype="float32", callback=self._callback,
            finished_callback=self._finished, extra_settings=extra, latency="low",
        )
        self._stream.start()
        log.info("audio capture started", extra={"device": self.device_index, "device_name": self.device_name,
                                                 "hostapi": self.hostapi, "sample_rate": self.sample_rate,
                                                 "block_size": self.block_size, "channels": self.channels})

    def stop(self) -> None:
        s, self._stream = self._stream, None
        if s is None:
            return
        try:
            s.abort(ignore_errors=True)
            s.close(ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("error while closing stream", extra={"error": str(exc)})
        log.info("audio capture stopped", extra={"device": self.device_index})

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d.update(device_index=self.device_index, device_name=self.device_name, hostapi=self.hostapi)
        if self._stream is not None:
            try:
                d["latency_s"] = round(float(self._stream.latency), 4)
            except Exception:  # noqa: BLE001
                pass
        return d
