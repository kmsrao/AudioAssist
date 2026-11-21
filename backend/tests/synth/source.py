"""Synthetic multichannel audio source for tests (no hardware)."""
from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from app.audio.sources import AudioSource, CaptureStats
from tests.synth.scenarios import build_channel_generators


class SyntheticSource(AudioSource):
    """Generates synthetic audio on a background thread paced to real time.

    Mimics a device callback (fixed-size blocks at the device block rate) so
    the rest of the pipeline cannot tell the difference."""

    kind = "synthetic"

    def __init__(self, sample_rate: int, block_size: int, channels: int, scenario: str = "demo",
                 realtime: bool = True, loop: bool = True):
        super().__init__(sample_rate, block_size, channels)
        self.scenario = scenario
        self.realtime = realtime
        self._gens = build_channel_generators(scenario, sample_rate, channels)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._block = np.zeros((block_size, channels), dtype=np.float32)
        self._started = False
        loop_s = min(30.0, 240.0 / max(channels, 1)) if loop else 0.0  # cap memory at ~46 MB
        self._loop = np.zeros((int(loop_s * sample_rate), channels), dtype=np.float32) if loop_s else None
        self._loop_filled = 0
        self._loop_pos = 0

    @property
    def running(self) -> bool:
        if not self.realtime:
            return self._started
        return self._thread is not None and self._thread.is_alive()

    def generate_block(self) -> np.ndarray:
        for ch, g in enumerate(self._gens):
            self._block[:, ch] = g.render(self.block_size)
        return self._block

    def pump(self, blocks: int) -> None:
        """Synchronously push ``blocks`` blocks (used by tests / non-realtime mode)."""
        for _ in range(blocks):
            self._deliver(self.generate_block())

    def _deliver(self, block: np.ndarray) -> None:
        t0 = time.perf_counter()
        self.ring.write(block)
        st = self.stats
        st.callbacks += 1
        st.frames += block.shape[0]
        st.last_callback_mono = time.monotonic()
        st.max_callback_ms = max(st.max_callback_ms, (time.perf_counter() - t0) * 1000.0)

    def _run(self) -> None:
        # Render several device blocks per wake-up (~40 ms): far fewer Python
        # calls / GIL hand-offs than one wake-up per 512-frame block, and the
        # analysis reads everything since its cursor anyway.
        per_wake = max(1, int(round(0.04 * self.sample_rate / self.block_size)))
        period = per_wake * self.block_size / self.sample_rate
        next_t = time.perf_counter()
        n = per_wake * self.block_size
        chunk = np.zeros((n, self.channels), dtype=np.float32)
        while not self._stop.is_set():
            self._next_chunk(chunk)
            self._deliver(chunk)
            self.stats.callbacks += per_wake - 1  # count in device-block units
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            elif delay < -0.5:  # fell badly behind (machine suspended?) - resync
                self.stats.input_overflows += 1
                next_t = time.perf_counter()

    def _next_chunk(self, out: np.ndarray) -> None:
        """Synthesis is CPU-heavy pure Python; to keep it from competing with the
        analysis thread for the GIL, the first ``loop`` seconds are synthesised
        once and cached, then played back as a seamless-enough loop."""
        n = out.shape[0]
        if self._loop is not None and self._loop_filled >= self._loop.shape[0]:
            L = self._loop.shape[0]
            first = min(n, L - self._loop_pos)
            out[:first] = self._loop[self._loop_pos:self._loop_pos + first]
            if first < n:
                out[first:] = self._loop[: n - first]
            self._loop_pos = (self._loop_pos + n) % L
            return
        for ch, g in enumerate(self._gens):  # one render call per channel
            out[:, ch] = g.render(n)
        if self._loop is not None:
            take = min(n, self._loop.shape[0] - self._loop_filled)
            self._loop[self._loop_filled:self._loop_filled + take] = out[:take]
            self._loop_filled += take

    def start(self) -> None:
        self.stats = CaptureStats(started_mono=time.monotonic())
        self._stop.clear()
        self.finished.clear()
        self._started = True
        if self.realtime:
            self._thread = threading.Thread(target=self._run, name="synthetic-audio", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._started = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d.update(device_name=f"Synthetic ({self.scenario})", hostapi="synthetic", scenario=self.scenario)
        return d
