"""Deterministic diagnostic rules.

Each rule inspects one channel's ``ChannelAnalysis`` in the context of its
``Profile`` and returns:

* ``None`` - the rule cannot be evaluated this tick (not enough data, e.g. the
  channel is silent). Such ticks neither confirm nor clear a diagnosis.
* ``RuleResult(triggered=False)`` - evaluated, condition not present.
* ``RuleResult(triggered=True, strength=s)`` - condition present this tick;
  ``s`` in [0, 1] says how far beyond the threshold the evidence is.

A single tick never produces a diagnosis; ``engine.DiagnosticEngine`` requires
the condition to persist. Thresholds are documented in DIAGNOSTIC_RULES.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from app.config.settings import AnalysisSettings
from app.diagnostics.profiles import Profile
from app.dsp.analyzer import ChannelAnalysis

SEVERITIES = ("low", "medium", "high", "critical")


@dataclass
class RuleResult:
    triggered: bool
    strength: float = 0.0
    severity: str = "low"
    magnitude: float = 0.0           # rule-specific primary measurement (documented per rule)
    unit: str = "dB"
    target_hz: float | None = None
    q_hint: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""


@dataclass
class RuleContext:
    settings: AnalysisSettings
    rate_hz: float

    @property
    def min_active_frames(self) -> int:
        return int(self.rate_hz * 2.0)


@dataclass(frozen=True)
class RuleSpec:
    key: str
    title: str
    group: str                       # tonal | dynamics | noise | feedback
    func: Callable[[ChannelAnalysis, Profile, RuleContext], RuleResult | None]
    persistence_factor: float = 1.0  # multiplies settings.diagnostic_persistence_s
    min_hits: int = 3


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _severity(value: float, cuts: tuple[float, float, float]) -> str:
    """Map a magnitude to low/medium/high/critical using ascending cut points."""
    if value >= cuts[2]:
        return "critical"
    if value >= cuts[1]:
        return "high"
    if value >= cuts[0]:
        return "medium"
    return "low"


def fmt_hz(f: float) -> str:
    return f"{f / 1000:.1f} kHz" if f >= 1000 else f"{f:.0f} Hz"


# --------------------------------------------------------------------------- tonal helpers
def deviation_curve(a: ChannelAnalysis, p: Profile) -> tuple[np.ndarray, np.ndarray, float]:
    """Measured long-term spectrum minus the profile reference, with the
    overall level offset removed (median over the profile's analysis range).
    Returns (freqs, deviation_db, offset_db)."""
    f = a.grid_hz
    diff = a.long_db - p.reference_db(f)
    lo, hi = p.analysis_range
    m = (f >= lo) & (f <= hi)
    offset = float(np.median(diff[m]))
    return f, diff - offset, offset


@dataclass
class Region:
    mean_db: float
    peak_db: float
    peak_hz: float
    width_lo_hz: float
    width_hi_hz: float

    @property
    def q(self) -> float:
        bw = max(self.width_hi_hz - self.width_lo_hz, 1e-6)
        return self.peak_hz / bw


def region_stats(f: np.ndarray, d: np.ndarray, lo: float, hi: float, find_min: bool = False) -> Region:
    m = (f >= lo) & (f <= hi)
    idx = np.flatnonzero(m)
    seg = -d[idx] if find_min else d[idx]
    k = idx[int(np.argmax(seg))]
    peak = d[k]
    # -3 dB (relative to the peak excess) extent of the bump on the deviation curve
    thr = peak - 3.0 if not find_min else peak + 3.0
    cmp = (lambda v: v >= thr) if not find_min else (lambda v: v <= thr)
    i0 = k
    while i0 > 0 and cmp(d[i0 - 1]):
        i0 -= 1
    i1 = k
    while i1 < len(d) - 1 and cmp(d[i1 + 1]):
        i1 += 1
    return Region(float(d[idx].mean()), float(peak), float(f[k]), float(f[i0]), float(f[i1]))


def grid_power_share(a: ChannelAnalysis, lo: float, hi: float, spectrum_db: np.ndarray | None = None) -> float:
    db = a.long_db if spectrum_db is None else spectrum_db
    f = a.grid_hz
    p = 10 ** (db / 10) * f  # grid points are log-spaced: weight by f to integrate per Hz
    tot = p.sum()
    if tot <= 0:
        return 0.0
    m = (f >= lo) & (f <= hi)
    return float(p[m].sum() / tot)


def _tonal_ready(a: ChannelAnalysis, ctx: RuleContext) -> bool:
    return a.active_frames >= ctx.min_active_frames and a.active


def _excess_rule(a: ChannelAnalysis, p: Profile, ctx: RuleContext, *, lo: float, hi: float, base_thr: float,
                 label: str, what: str, q_range: tuple[float, float]) -> RuleResult | None:
    if not _tonal_ready(a, ctx):
        return None
    f, d, _ = deviation_curve(a, p)
    r = region_stats(f, d, lo, hi)
    thr = base_thr + p.tolerance_db
    trig = r.peak_db >= thr and r.mean_db >= thr * 0.5
    strength = _clip01((r.peak_db - thr) / 6.0)
    ev = {"region_hz": [lo, hi], "peak_excess_db": round(r.peak_db, 1), "mean_excess_db": round(r.mean_db, 1),
          "peak_hz": round(r.peak_hz), "threshold_db": thr, "bump_width_hz": [round(r.width_lo_hz), round(r.width_hi_hz)],
          "profile_reference": p.name}
    return RuleResult(
        trig, strength, _severity(r.peak_db - thr, (2.0, 5.0, 99.0)), r.peak_db, "dB", r.peak_hz,
        float(np.clip(r.q, *q_range)), ev,
        f"Long-term energy around {fmt_hz(r.peak_hz)} is {r.peak_db:.1f} dB above the {p.name} reference shape "
        f"(threshold {thr:.1f} dB) - {what}.",
    )


# --------------------------------------------------------------------------- tonal rules
def rule_muddy(a, p, ctx):
    return _excess_rule(a, p, ctx, lo=200, hi=500, base_thr=7.0, label="muddy",
                        what="consistent with a muddy, congested low-mid", q_range=(0.7, 1.5))


def rule_boxy(a, p, ctx):
    return _excess_rule(a, p, ctx, lo=400, hi=800, base_thr=7.0, label="boxy",
                        what="consistent with a boxy / hollow coloration", q_range=(1.0, 2.0))


def rule_boomy(a, p, ctx):
    return _excess_rule(a, p, ctx, lo=100, hi=250, base_thr=7.0, label="boomy",
                        what="consistent with boominess (proximity effect, room or stage resonance)", q_range=(0.7, 1.4))


def rule_low_mid_excess(a, p, ctx):
    return _excess_rule(a, p, ctx, lo=150, hi=600, base_thr=5.0, label="low_mid_excess",
                        what="broad low-mid build-up", q_range=(0.5, 1.0))


def rule_harsh(a, p, ctx):
    return _excess_rule(a, p, ctx, lo=2500, hi=5000, base_thr=7.0, label="harsh",
                        what="consistent with harshness / listening fatigue", q_range=(1.5, 3.0))


def rule_weak_presence(a, p, ctx):
    if not _tonal_ready(a, ctx):
        return None
    f, d, _ = deviation_curve(a, p)
    r = region_stats(f, d, 1000, 3000, find_min=True)
    thr = 6.0 + p.tolerance_db
    deficit = -r.mean_db
    trig = deficit >= thr
    return RuleResult(
        trig, _clip01((deficit - thr) / 6.0), _severity(deficit - thr, (2.0, 6.0, 99.0)), deficit, "dB",
        r.peak_hz, 1.0,
        {"region_hz": [1000, 3000], "mean_deficit_db": round(deficit, 1), "lowest_hz": round(r.peak_hz),
         "threshold_db": thr, "profile_reference": p.name},
        f"The 1-3 kHz presence region averages {deficit:.1f} dB below the {p.name} reference shape "
        f"(threshold {thr:.1f} dB) - intelligibility/presence may be reduced.",
    )


def rule_sibilance(a, p, ctx):
    """Sibilance is transient, so it is judged on the loudest 5-9 kHz bursts
    (95th percentile of per-frame 5-9 kHz energy) relative to the channel's
    typical 200 Hz-2 kHz body energy (median), not on the averaged spectrum."""
    if a.sibilance_p90_db is None or a.active_frames < ctx.min_active_frames * 2:
        return None
    thr = 10.0 + p.tolerance_db
    v = a.sibilance_p90_db
    trig = v >= thr
    target = None
    if trig:
        f, d, _ = deviation_curve(a, p)
        target = region_stats(f, d, 5000, 9000).peak_hz
    return RuleResult(
        trig, _clip01((v - thr) / 6.0), _severity(v - thr, (3.0, 6.0, 99.0)), v, "dB", target or 6500.0, 3.0,
        {"p95_5k_9k_vs_typical_body_db": round(v, 1), "median_5k_9k_vs_body_db": round(a.sibilance_median_db or 0.0, 1),
         "threshold_db": thr},
        f"The loudest 5% of 5-9 kHz bursts reach {v:+.1f} dB relative to the channel's typical 200 Hz-2 kHz body "
        f"level (threshold {thr:+.1f} dB) - sibilant bursts may be excessive.",
    )


def rule_spectral_concentration(a, p, ctx):
    if not _tonal_ready(a, ctx):
        return None
    f = a.grid_hz
    best_share, best_f = 0.0, 0.0
    for fc in f[(f >= 40) & (f <= 12000)][::4]:
        sh = grid_power_share(a, fc / 2 ** (1 / 6), fc * 2 ** (1 / 6))
        if sh > best_share:
            best_share, best_f = sh, float(fc)
    thr = 0.55 + 0.02 * p.tolerance_db
    trig = best_share >= thr
    return RuleResult(
        trig, _clip01((best_share - thr) / 0.3), _severity(best_share - thr, (0.1, 0.25, 9.0)), best_share * 100, "%",
        best_f, 1.0, {"one_third_octave_share_pct": round(best_share * 100, 1), "center_hz": round(best_f),
                      "threshold_pct": round(thr * 100)},
        f"{best_share * 100:.0f}% of the channel's long-term energy sits in one 1/3-octave around {fmt_hz(best_f)} "
        f"(threshold {thr * 100:.0f}%) - the tone may be dominated by a narrow region.",
    )


# --------------------------------------------------------------------------- low-frequency / noise rules
def rule_rumble(a, p, ctx):
    if a.active_frames < ctx.min_active_frames and a.noise_db is None:
        return None
    limit = p.rumble_limit_hz
    share_active = grid_power_share(a, 20, limit) if a.active_frames >= ctx.min_active_frames else 0.0
    share_noise = grid_power_share(a, 20, limit, a.noise_db) if a.noise_db is not None else 0.0
    share = max(share_active, share_noise)
    sub_level = float(a.band_levels_long[0]) if len(a.band_levels_long) else -200.0
    thr = 0.15
    trig = share >= thr and sub_level > -70.0
    lo_hpf, hi_hpf = p.hpf_range
    s = _clip01((share - thr) / 0.35)
    cutoff = lo_hpf + (hi_hpf - lo_hpf) * (0.5 if s > 0.5 else 0.0)
    cutoff = float(np.clip(cutoff, max(ctx.settings.hpf_min_hz, limit), ctx.settings.hpf_max_hz))
    return RuleResult(
        trig, s, _severity(share, (0.25, 0.45, 9.0)), share * 100, "%", cutoff, None,
        {"energy_below_hz": limit, "share_pct_active": round(share_active * 100, 1),
         "share_pct_noise": round(share_noise * 100, 1), "band_20_60_dbfs": round(sub_level, 1),
         "suggested_hpf_hz": round(cutoff), "threshold_pct": thr * 100},
        f"{share * 100:.0f}% of the energy lies below {limit:.0f} Hz, where a {p.name} source has little useful "
        f"content - likely low-frequency rumble (handling, stage vibration, HVAC, wind).",
    )


def _mains_candidates(ctx: RuleContext) -> list[str]:
    m = ctx.settings.mains_frequency
    return ["50.0", "60.0"] if m == "auto" else [f"{float(m):.1f}"]


def rule_hum(a, p, ctx):
    if a.tick < ctx.rate_hz * 2:
        return None
    best = None
    source = "all frames"
    bass_like = p.key in ("bass", "kick", "keyboard")
    for key in _mains_candidates(ctx):
        cands = []
        if a.hum_noise is not None:
            cands.append((a.hum_noise[key], "pauses (noise floor)"))
        if not bass_like:  # musical bass notes can sit on mains harmonics; only trust pauses there
            cands.append((a.hum[key], "all frames"))
        for res, src in cands:
            if best is None or res.score > best.score:
                best, source = res, src
    if best is None:
        return None
    audible = best.fundamental_level_db > -95.0
    # >=3 harmonics, or a very clear fundamental + 2nd harmonic (other harmonics may be masked by programme)
    strong_pair = len(best.prominences_db) >= 2 and best.prominences_db[0] >= 15.0 and best.prominences_db[1] >= 10.0
    trig = (best.harmonics_found >= 3 or strong_pair) and audible
    s = _clip01((best.score - 10.0) / 12.0)
    return RuleResult(
        trig, s, _severity(best.score, (12.0, 20.0, 99.0)), best.score, "dB", best.mains_hz, 10.0,
        {"mains_hz": best.mains_hz, "harmonics_detected": best.harmonics_found,
         "harmonic_prominence_db": [round(x, 1) for x in best.prominences_db],
         "fundamental_level_dbfs": round(best.fundamental_level_db, 1), "measured_on": source},
        f"Stable peaks at {best.mains_hz:.0f} Hz and its harmonics ({best.harmonics_found} harmonics >= 10 dB above "
        f"the surrounding spectrum, measured on {source}) - possible mains hum / ground loop.",
    )


def rule_broadband_noise(a, p, ctx):
    ns = a.noise_stats
    if ns is None:
        return None
    lvl = ns["level_dbfs"]
    thr = -60.0
    broadband = ns["flatness"] >= 0.2 and ns["hf_share"] <= 0.6
    trig = lvl >= thr and broadband
    snr = a.snr_estimate_db
    s = _clip01((lvl - thr) / 15.0)
    if snr is not None and snr < 20:
        s = _clip01(s + 0.2)
    return RuleResult(
        trig, s, _severity(lvl - thr, (6.0, 12.0, 99.0)), lvl, "dBFS", None, None,
        {"noise_floor_dbfs": round(lvl, 1), "flatness": round(ns["flatness"], 3), "hf_share": round(ns["hf_share"], 2),
         "snr_estimate_db": None if snr is None else round(snr, 1), "threshold_dbfs": thr},
        f"Between phrases the channel's floor sits at {lvl:.1f} dBFS with a broadband (flat) character "
        f"(threshold {thr:.0f} dBFS)" + (f"; estimated signal-to-noise {snr:.0f} dB" if snr is not None else "") + ".",
    )


def rule_hiss(a, p, ctx):
    ns = a.noise_stats
    if ns is None:
        return None
    lvl = ns["level_dbfs"]
    thr = -66.0
    trig = lvl >= thr and ns["hf_share"] > 0.6
    return RuleResult(
        trig, _clip01((lvl - thr) / 15.0), _severity(lvl - thr, (6.0, 12.0, 99.0)), lvl, "dBFS", 6000.0, None,
        {"noise_floor_dbfs": round(lvl, 1), "hf_share": round(ns["hf_share"], 2), "threshold_dbfs": thr},
        f"The noise floor between phrases ({lvl:.1f} dBFS) is dominated by energy above 4 kHz "
        f"({ns['hf_share'] * 100:.0f}%) - possible hiss (high preamp gain, noisy source or DI).",
    )


# --------------------------------------------------------------------------- dynamics rules
def rule_clipping(a, p, ctx):
    ticks = a.clip_ticks_recent
    run = a.max_clip_run
    trig = ticks >= 2 and run >= 3
    return RuleResult(
        trig, _clip01(0.4 + ticks / 20.0), "critical" if ticks >= 10 else "high", float(ticks), "events", None, None,
        {"clip_events_last_5s": ticks, "clipped_samples_last_5s": a.clipped_samples_recent,
         "longest_clipped_run_samples": run, "peak_hold_dbfs": round(a.peak_hold_dbfs, 1)},
        f"{ticks} analysis periods in the last 5 s contained consecutive full-scale samples (longest run {run} "
        f"samples) - the input is likely clipping at the preamp/converter.",
    )


def rule_low_headroom(a, p, ctx):
    if a.active_frames < ctx.min_active_frames:
        return None
    r = a.near_overload_ratio
    trig = r >= 0.2 and a.clip_ticks_recent == 0
    return RuleResult(
        trig, _clip01((r - 0.2) / 0.5), _severity(r, (0.4, 0.7, 9.0)), r * 100, "%", None, None,
        {"active_periods_with_peaks_above_-1dBFS_pct": round(r * 100, 1), "peak_hold_dbfs": round(a.peak_hold_dbfs, 1)},
        f"Peaks exceed -1 dBFS in {r * 100:.0f}% of active periods - very little headroom left before clipping.",
    )


def rule_weak_signal(a, p, ctx):
    sig = a.signal_dbfs
    if sig is None or a.tick < ctx.rate_hz * 5:
        return None
    gate = ctx.settings.activity_gate_dbfs
    if sig < gate:
        return None  # nothing meaningful on the channel - not a gain-staging question
    target = p.target_rms_dbfs
    shortfall = target - sig
    trig = shortfall >= 18.0 and a.peak_hold_dbfs < -20.0
    return RuleResult(
        trig, _clip01((shortfall - 18.0) / 12.0), _severity(shortfall, (22.0, 28.0, 99.0)), shortfall, "dB", None, None,
        {"signal_p90_rms_dbfs": round(sig, 1), "profile_nominal_rms_dbfs": target,
         "peak_hold_dbfs": round(a.peak_hold_dbfs, 1),
         "noise_floor_dbfs": None if a.noise_floor_dbfs is None else round(a.noise_floor_dbfs, 1)},
        f"The channel's typical active level is {sig:.1f} dBFS RMS, about {shortfall:.0f} dB below the nominal "
        f"{target:.0f} dBFS for a {p.name} source - input gain may be too low.",
    )


# --------------------------------------------------------------------------- feedback
def rule_feedback_risk(a, p, ctx):
    """Feedback-risk needs *all* of: persistence, narrowness, frequency
    stability and prominence, accumulated over seconds. Harmonic membership
    (musical notes) and the profile's sensitivity reduce the evidence."""
    if p.feedback_sensitivity <= 0:
        return None
    if a.rms_dbfs < ctx.settings.activity_gate_dbfs:
        return RuleResult(False)
    min_obs = float(np.clip(2.0 / max(p.feedback_sensitivity, 0.1), 1.5, 5.0))
    best, best_s = None, 0.0
    considered = []
    for t in a.tracks:
        if not (100 <= t["freq_hz"] <= 12000):
            continue
        if t["observed_s"] < min_obs or t["persistence"] < 0.85:
            continue
        narrow = t["resolution_limited"] or t["q"] >= 20
        if not narrow or t["freq_stability"] > 0.003 or t["prominence_db"] < 15:
            continue
        prom_t = _clip01((t["prominence_db"] - 15) / 20)
        pers_t = _clip01((t["persistence"] - 0.85) / 0.15)
        grow_t = _clip01(t["level_trend_db_per_s"] / 3.0)
        s = p.feedback_sensitivity * (0.4 + 0.3 * prom_t + 0.2 * pers_t + 0.1 * grow_t)
        if t["harmonic"]:
            s *= 0.5
        considered.append({**t, "score": round(s, 2)})
        if s > best_s:
            best, best_s = t, s
    thr = 0.4
    if best is None or best_s < thr:
        return RuleResult(False, evidence={"candidates": considered[:5]})
    q = 10.0 if best["resolution_limited"] else float(np.clip(best["q"], 8, 16))
    return RuleResult(
        True, _clip01((best_s - thr) / 0.5), "critical" if best["level_trend_db_per_s"] > 1.5 else "high",
        best["prominence_db"], "dB", best["freq_hz"], q,
        {"freq_hz": best["freq_hz"], "prominence_db": best["prominence_db"], "persistence": best["persistence"],
         "observed_s": best["observed_s"], "freq_stability": best["freq_stability"],
         "level_trend_db_per_s": best["level_trend_db_per_s"], "harmonic_series": best["harmonic"],
         "q_measured": best["q"], "resolution_limited": best["resolution_limited"],
         "min_observation_s": min_obs, "other_candidates": [c for c in considered if c["id"] != best["id"]][:4]},
        f"A narrow peak at {fmt_hz(best['freq_hz'])} has been present {best['persistence'] * 100:.0f}% of the time for "
        f"{best['observed_s']:.1f} s, {best['prominence_db']:.0f} dB above its surroundings, with a stable frequency"
        + (f" and rising {best['level_trend_db_per_s']:.1f} dB/s" if best["level_trend_db_per_s"] > 0.3 else "")
        + (" (not part of a harmonic series)" if not best["harmonic"] else " (part of a harmonic series - could be a sustained note)")
        + " - possible feedback build-up.",
    )


RULES: dict[str, RuleSpec] = {r.key: r for r in [
    RuleSpec("muddy", "Potential muddiness", "tonal", rule_muddy),
    RuleSpec("boxy", "Potential boxiness", "tonal", rule_boxy),
    RuleSpec("boomy", "Potential boominess", "tonal", rule_boomy),
    RuleSpec("low_mid_excess", "Excess low-mid energy", "tonal", rule_low_mid_excess),
    RuleSpec("harsh", "Potential harshness", "tonal", rule_harsh),
    RuleSpec("sibilance", "Possible excessive sibilance", "tonal", rule_sibilance, persistence_factor=1.5),
    RuleSpec("weak_presence", "Possibly weak presence", "tonal", rule_weak_presence, persistence_factor=1.5),
    RuleSpec("spectral_concentration", "Excessive spectral concentration", "tonal", rule_spectral_concentration),
    RuleSpec("rumble", "Low-frequency rumble", "noise", rule_rumble),
    RuleSpec("hum", "Possible mains hum", "noise", rule_hum),
    RuleSpec("broadband_noise", "Possible broadband noise", "noise", rule_broadband_noise, persistence_factor=1.5),
    RuleSpec("hiss", "Possible hiss", "noise", rule_hiss, persistence_factor=1.5),
    RuleSpec("clipping", "Clipping", "dynamics", rule_clipping, persistence_factor=0.2, min_hits=2),
    RuleSpec("low_headroom", "Low headroom", "dynamics", rule_low_headroom),
    RuleSpec("weak_signal", "Weak signal level", "dynamics", rule_weak_signal, persistence_factor=2.0),
    RuleSpec("feedback_risk", "Possible feedback risk", "feedback", rule_feedback_risk, persistence_factor=0.5),
]}
