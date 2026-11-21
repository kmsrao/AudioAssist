"""Time-domain level / dynamics measurements."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-12


def to_dbfs(x: np.ndarray | float) -> np.ndarray | float:
    return 20.0 * np.log10(np.maximum(x, EPS))


def rms(x: np.ndarray, axis: int = 0) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    return np.sqrt(np.mean(x * x, axis=axis))


def peak(x: np.ndarray, axis: int = 0) -> np.ndarray:
    return np.max(np.abs(x), axis=axis)


def crest_factor_db(x: np.ndarray, axis: int = 0) -> np.ndarray:
    return to_dbfs(peak(x, axis)) - to_dbfs(rms(x, axis))


def longest_run(mask: np.ndarray) -> int:
    """Length of the longest run of True values in a 1-D boolean array."""
    if not mask.any():
        return 0
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return int((ends - starts).max())


@dataclass
class BlockLevels:
    rms_dbfs: np.ndarray        # per channel
    peak_dbfs: np.ndarray
    crest_db: np.ndarray
    clipped_samples: np.ndarray  # samples at/above clip threshold
    max_clip_run: np.ndarray     # longest consecutive clipped run
    frames: int


def measure_block(x: np.ndarray, clip_threshold: float = 0.999) -> BlockLevels:
    """x: (frames, channels). All samples since the previous tick are measured,
    so short overs between FFT windows are not missed."""
    if x.ndim == 1:
        x = x[:, None]
    n, ch = x.shape
    if n == 0:
        z = np.full(ch, -240.0)
        return BlockLevels(z, z.copy(), np.zeros(ch), np.zeros(ch, int), np.zeros(ch, int), 0)
    r = rms(x)
    p = peak(x)
    over = np.abs(x) >= clip_threshold
    clipped = over.sum(axis=0)
    runs = np.array([longest_run(over[:, c]) if clipped[c] else 0 for c in range(ch)])
    r_db, p_db = to_dbfs(r), to_dbfs(p)
    return BlockLevels(r_db, p_db, p_db - r_db, clipped, runs, n)
