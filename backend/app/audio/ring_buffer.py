"""Bounded multichannel ring buffer shared between the audio callback and the
analysis thread.

Design constraints (see ARCHITECTURE.md):

* The writer (PortAudio callback) must never block for long and never
  allocate: ``write`` copies into a preallocated array with at most two slice
  assignments while holding a lock for a few microseconds.
* The buffer is bounded. If the reader falls behind by more than the
  capacity, the oldest samples are overwritten and the reader is told how many
  frames it missed (``frames_lost``) - capture never stalls because analysis is
  slow.
* Readers use an absolute frame cursor so several readers could consume
  independently.
"""
from __future__ import annotations

import threading

import numpy as np


class MultichannelRingBuffer:
    def __init__(self, capacity_frames: int, channels: int, dtype=np.float32):
        if capacity_frames <= 0 or channels <= 0:
            raise ValueError("capacity and channels must be positive")
        self.capacity = int(capacity_frames)
        self.channels = int(channels)
        self._buf = np.zeros((self.capacity, self.channels), dtype=dtype)
        self._lock = threading.Lock()
        self._total_written = 0  # absolute frame counter

    @property
    def total_written(self) -> int:
        return self._total_written

    def write(self, block: np.ndarray) -> None:
        """Called from the audio callback. ``block`` shape: (frames, channels)."""
        n = block.shape[0]
        if n == 0:
            return
        if n > self.capacity:  # pathological: keep the newest samples only
            block = block[-self.capacity :]
            skipped = n - self.capacity
            n = self.capacity
        else:
            skipped = 0
        ch = min(block.shape[1], self.channels)
        with self._lock:
            start = (self._total_written + skipped) % self.capacity
            first = min(n, self.capacity - start)
            self._buf[start : start + first, :ch] = block[:first, :ch]
            if first < n:
                self._buf[: n - first, :ch] = block[first:n, :ch]
            self._total_written += n + skipped

    def read_since(self, cursor: int, max_frames: int | None = None) -> tuple[np.ndarray, int, int]:
        """Return (frames, new_cursor, frames_lost) for everything written
        after ``cursor``. Allocates a new array (reader side only)."""
        with self._lock:
            total = self._total_written
            available = total - cursor
            lost = 0
            if available > self.capacity:
                lost = available - self.capacity
                cursor = total - self.capacity
                available = self.capacity
            if max_frames is not None and available > max_frames:
                # caller only wants the newest max_frames; older ones are skipped, not lost
                cursor = total - max_frames
                available = max_frames
            out = self._copy_range(cursor, available)
        return out, total, lost

    def latest(self, n: int) -> np.ndarray:
        """Most recent ``n`` frames (zero-padded at the front if not yet filled)."""
        n = min(n, self.capacity)
        with self._lock:
            have = min(n, self._total_written)
            data = self._copy_range(self._total_written - have, have)
        if have < n:
            pad = np.zeros((n - have, self.channels), dtype=self._buf.dtype)
            data = np.concatenate([pad, data], axis=0)
        return data

    def _copy_range(self, start_abs: int, count: int) -> np.ndarray:
        if count <= 0:
            return np.zeros((0, self.channels), dtype=self._buf.dtype)
        start = start_abs % self.capacity
        first = min(count, self.capacity - start)
        if first == count:
            return self._buf[start : start + count].copy()
        return np.concatenate([self._buf[start:], self._buf[: count - first]], axis=0)
