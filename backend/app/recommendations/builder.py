"""Converts a diagnostic into a concrete, conservative operator action.

Rules of thumb implemented here (see DIAGNOSTIC_RULES.md):

* Prefer cuts over boosts; boosts are small and only suggested when no
  competing cut is pending ("cut before boost").
* EQ cut depth ~ half the measured excess, in 0.5 dB steps, between 2 dB and
  the configured maximum cut. Boost depth ~ 0.4 x deficit, capped at the
  configured maximum boost.
* Q comes from the measured width of the deviation (clamped to a per-issue
  range), feedback notches from the measured peak width (Q 8-16).
* Nothing is ever applied automatically - these are suggestions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import AnalysisSettings
from app.diagnostics.engine import Diagnostic, confidence_label
from app.diagnostics.rules import fmt_hz


def round_half(x: float) -> float:
    return round(x * 2) / 2


def round_freq(f: float, precise: bool = False) -> float:
    if f < 100:
        return float(round(f))
    if f < 1000:
        return float(round(f) if precise else round(f / 5) * 5)
    if f < 10000:
        return float(round(f / 5) * 5 if precise else round(f / 10) * 10)
    return float(round(f / 100) * 100)


@dataclass
class Action:
    control: str
    filter_type: str | None = None   # bell | notch | high_pass | high_shelf | dynamic | gain | None
    target_hz: float | None = None
    gain_db: float | None = None
    q: float | None = None
    hpf_hz: float | None = None
    hpf_slope: str | None = None
    gain_change_db: float | None = None
    steps: list[str] = field(default_factory=list)
    expected: str = ""
    caveats: list[str] = field(default_factory=list)
    fallback: dict[str, Any] | None = None


def _cut_db(excess: float, s: AnalysisSettings) -> float:
    return -min(s.max_eq_cut_db, max(2.0, round_half(abs(excess) * 0.5)))


def _eq_cut(d: Diagnostic, s: AnalysisSettings, expected: str, extra_steps: list[str] | None = None,
            caveats: list[str] | None = None) -> Action:
    f = round_freq(d.target_hz or 1000.0)
    g = _cut_db(d.magnitude, s)
    q = round(d.q_hint or 1.0, 1)
    return Action(
        control="Channel EQ - parametric band", filter_type="bell", target_hz=f, gain_db=g, q=q,
        steps=[f"Set a bell band at ~{fmt_hz(f)}, Q {q}, and cut {abs(g):g} dB.",
               "Sweep the band +/-1/3 octave while listening; keep it where the problem is most audible.",
               *(extra_steps or []),
               "Mark the recommendation as applied so the analyzer can verify the result."],
        expected=expected, caveats=caveats or [],
    )


def build_action(d: Diagnostic, s: AnalysisSettings) -> Action:
    c = d.category
    ev = d.evidence
    if c == "muddy":
        return _eq_cut(d, s, "Clearer low-mid, less congestion; improved intelligibility and separation.")
    if c == "boxy":
        return _eq_cut(d, s, "Less boxy / hollow coloration; a more open, natural tone.")
    if c == "boomy":
        return _eq_cut(d, s, "Tighter, less boomy low end without losing body.",
                       extra_steps=["If the source is close-mic'd, a little more distance also reduces proximity effect."])
    if c == "low_mid_excess":
        return _eq_cut(d, s, "Reduced low-mid build-up; more room for other sources in the mix.")
    if c == "harsh":
        return _eq_cut(d, s, "Smoother upper mids, less listening fatigue.",
                       caveats=["If harshness only appears on loud passages, a dynamic EQ band is gentler than a static cut."])
    if c == "spectral_concentration":
        a = _eq_cut(d, s, "More even tonal balance.")
        a.gain_db = max(a.gain_db or -2.0, -3.0)
        a.steps[0] = f"Investigate the region around {fmt_hz(a.target_hz)}; if it sounds dominant, cut {abs(a.gain_db):g} dB, Q {a.q}."
        return a
    if c == "sibilance":
        f = round_freq(d.target_hz or 6500.0)
        return Action(
            control="De-esser (or dynamic EQ band)", filter_type="dynamic", target_hz=f, gain_db=-3.0, q=3.0,
            steps=[f"Set a de-esser / dynamic EQ band at ~{fmt_hz(f)} and aim for about 3 dB of reduction on 's' sounds only.",
                   "Check that the voice does not start to lisp or sound dull."],
            expected="Softer 's'/'t' sounds without dulling the voice.",
            fallback={"control": "Channel EQ - parametric band", "filter_type": "bell", "target_hz": f,
                      "gain_db": -2.5, "q": 3.0, "note": "Static alternative if no de-esser is available."},
        )
    if c == "weak_presence":
        f = round_freq(min(max(d.target_hz or 2000.0, 1500.0), 3000.0))
        g = min(s.max_eq_boost_db, max(1.0, round_half(abs(d.magnitude) * 0.4)))
        return Action(
            control="Channel EQ - parametric band", filter_type="bell", target_hz=f, gain_db=g, q=1.0,
            steps=[f"Try a gentle boost of {g:g} dB at ~{fmt_hz(f)}, Q 1.0.",
                   "Compare against simply cutting low-mid energy first - often the better fix."],
            expected="Improved presence and intelligibility.",
            caveats=["Boosts reduce gain-before-feedback on open microphones; watch for ringing."],
        )
    if c == "rumble":
        hz = round_freq(ev.get("suggested_hpf_hz", d.target_hz or 80.0))
        return Action(
            control="Channel high-pass filter (HPF)", filter_type="high_pass", hpf_hz=hz, hpf_slope="12-18 dB/oct",
            steps=[f"Engage the channel HPF at ~{hz:g} Hz.",
                   "Raise it slowly until the source starts to thin out, then back off slightly.",
                   "Also check stand/mount isolation, handling noise and HVAC/wind sources."],
            expected="Less low-frequency rumble; cleaner low end and more headroom.",
        )
    if c == "hum":
        f = float(ev.get("mains_hz", d.target_hz or 60.0))
        return Action(
            control="Source / cabling (ground loop)", filter_type=None,
            steps=["Check cable and connector integrity; reseat or swap the cable.",
                   "On DI boxes try the ground-lift switch; keep audio and power cables apart and avoid dimmer circuits.",
                   "Power the source and the console from the same circuit where possible."],
            expected="Removal of the hum at its source (preferred over EQ).",
            fallback={"control": "Channel EQ - narrow notches", "filter_type": "notch", "target_hz": f,
                      "gain_db": -6.0, "q": 10.0, "also_hz": [2 * f],
                      "note": f"Only if the source cannot be fixed: notch {f:g} Hz and {2 * f:g} Hz, Q 10, -6 dB."},
        )
    if c == "broadband_noise":
        return Action(
            control="Gain structure / source", filter_type=None,
            steps=["Check the source, cable, phantom power and nearby interference.",
                   "Verify gain staging: preamp gain should bring the source to a healthy level, not amplify a weak signal plus noise.",
                   "If the channel is idle between uses, consider muting it or using a gate/expander (operator decision)."],
            expected="A lower noise floor between phrases.",
            caveats=["Noise floor is estimated from quiet periods; it is not a calibrated SNR measurement."],
        )
    if c == "hiss":
        return Action(
            control="Gain structure / source", filter_type="high_shelf", target_hz=8000.0, gain_db=-2.0, q=0.7,
            steps=["Check preamp gain and the source's own noise (DI, wireless receiver, keyboard output level).",
                   "Only if the content allows: a gentle high-shelf cut (~-2 dB above 8 kHz)."],
            expected="Less audible hiss.",
        )
    if c == "clipping":
        g = -6.0 if ev.get("clip_events_last_5s", 0) >= 5 else -3.0
        return Action(
            control="Input gain / trim (preamp)", filter_type="gain", gain_change_db=g,
            steps=[f"Reduce the input gain (trim) by about {abs(g):g} dB, or engage the pad.",
                   "Re-balance with the fader if needed.", "Check the source's own output level (instrument, DI, playback)."],
            expected="Elimination of clipping distortion.",
            caveats=["Clipping that happens before the console input (in the source or DI) is not fixed by trim."],
        )
    if c == "low_headroom":
        return Action(
            control="Input gain / trim (preamp)", filter_type="gain", gain_change_db=-3.0,
            steps=["Reduce input gain by about 3 dB to restore headroom.", "Compensate with the fader."],
            expected="Safer headroom on peaks; lower risk of clipping.",
        )
    if c == "weak_signal":
        shortfall = float(ev.get("profile_nominal_rms_dbfs", -24) - ev.get("signal_p90_rms_dbfs", -60))
        headroom = -6.0 - float(ev.get("peak_hold_dbfs", -60))
        g = max(3.0, min(shortfall - 6.0, headroom, s.max_gain_change_db))
        g = float(round(g))
        return Action(
            control="Input gain / trim (preamp)", filter_type="gain", gain_change_db=g,
            steps=[f"Increase input gain by about {g:g} dB while watching peaks (keep them below about -6 dBFS).",
                   "Check that the correct source is patched and that the mic is positioned on-axis and close enough."],
            expected="Better signal-to-noise and more consistent level.",
        )
    if c == "feedback_risk":
        f = round_freq(d.target_hz or 1000.0, precise=True)
        g = -6.0 if d.severity == "critical" else -3.0
        g = max(g, -s.max_eq_cut_db)
        q = round(d.q_hint or 10.0, 1)
        return Action(
            control="Narrow EQ cut (channel, monitor or system graphic EQ)", filter_type="notch", target_hz=f,
            gain_db=g, q=q,
            steps=["First: check mic position relative to wedges/speakers and reduce the monitor send if it is ringing.",
                   f"Then apply a narrow cut of {abs(g):g} dB at {fmt_hz(f)}, Q {q}, on the path that is ringing.",
                   "Re-check: if the ring moves to a new frequency, the overall gain is too high."],
            expected="More stability margin (gain-before-feedback) at that frequency.",
            caveats=["If this frequency is a sustained musical note rather than a ring, dismiss the recommendation."],
        )
    return Action(control="Investigate", steps=["Inspect the channel; no specific action defined."])


def recommendation_fields(d: Diagnostic, s: AnalysisSettings) -> dict[str, Any]:
    a = build_action(d, s)
    return {
        "control": a.control, "filter_type": a.filter_type, "target_hz": a.target_hz, "gain_db": a.gain_db,
        "q": a.q, "hpf_hz": a.hpf_hz, "hpf_slope": a.hpf_slope, "gain_change_db": a.gain_change_db,
        "actions": a.steps, "expected_improvement": a.expected, "caveats": a.caveats, "fallback": a.fallback,
        "explanation": d.explanation, "issue": d.title, "severity": d.severity, "confidence": d.confidence,
        "confidence_label": confidence_label(d.confidence),
        "supporting_analysis": {"evidence": d.evidence, "confidence": d.confidence_breakdown,
                                "measured": {"value": round(d.magnitude, 2), "unit": d.unit}},
    }


def summary_line(fields: dict[str, Any]) -> str:
    ft = fields.get("filter_type")
    if ft in ("bell", "notch", "high_shelf") and fields.get("target_hz"):
        return f"{fields['control']}: {fields['gain_db']:+g} dB at {fmt_hz(fields['target_hz'])}, Q {fields['q']}"
    if ft == "high_pass":
        return f"{fields['control']}: HPF at {fields['hpf_hz']:g} Hz"
    if ft == "gain":
        return f"{fields['control']}: {fields['gain_change_db']:+g} dB"
    if ft == "dynamic":
        return f"{fields['control']} at {fmt_hz(fields['target_hz'])}"
    return fields["control"]
