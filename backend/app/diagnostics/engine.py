"""Diagnostic engine: turns per-tick rule results into persistent, explainable
diagnostics with explicit confidence.

Lifecycle per (channel, rule):

    idle --(hit ratio >= 0.7 over the persistence window)--> active
    active --(hit ratio < 0.25 over the clear window)------> cleared

Confidence (all terms in [0, 1], formula exposed in every diagnostic):

    evidence    = 0.4 + 0.6 * mean(strength of triggered ticks in window)
    persistence = triggered ticks / evaluable ticks in window
    context     = profile_weight * data_sufficiency
                  profile_weight    = 1.0 if the rule is a priority for the
                                      channel profile, else 0.75
                  data_sufficiency  = min(1, evaluable ticks / window length)
    confidence  = 0.5 * evidence + 0.3 * persistence + 0.2 * context
"""
from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config.settings import AnalysisSettings
from app.diagnostics.profiles import Profile, get_profile
from app.diagnostics.rules import RULES, RuleContext, RuleResult, RuleSpec
from app.dsp.analyzer import ChannelAnalysis
from app.logging.setup import get_logger
from app.utils.timeutil import now

log = get_logger("diagnostics")

CONFIRM_RATIO = 0.7
CLEAR_RATIO = 0.25
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
# geometric centres of the heuristic investigation ranges
_LOWMID_CENTER = {"boomy": 158.0, "muddy": 316.0, "boxy": 566.0, "low_mid_excess": 300.0}

W_EVIDENCE, W_PERSISTENCE, W_CONTEXT = 0.5, 0.3, 0.2


def confidence_label(c: float) -> str:
    return "high" if c >= 0.75 else "moderate" if c >= 0.5 else "low"


@dataclass
class Diagnostic:
    id: str
    channel: int                 # 1-based channel number
    channel_name: str
    profile: str
    category: str
    title: str
    group: str
    severity: str
    confidence: float
    confidence_breakdown: dict[str, Any]
    first_seen: float
    last_seen: float
    evidence: dict[str, Any]
    explanation: str
    target_hz: float | None
    q_hint: float | None
    magnitude: float
    unit: str
    status: str = "active"       # active | cleared
    suppressed_by: str | None = None
    recommended_action: str = ""
    cleared_at: float | None = None
    age_s: float = 0.0           # time active, measured in analysed audio time (not wall clock)
    unsuppressed_s: float = 0.0  # continuous time without being suppressed by a related diagnosis

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["confidence_label"] = confidence_label(self.confidence)
        d["timestamp"] = self.last_seen
        return d


@dataclass
class _Tracker:
    spec: RuleSpec
    window: deque = field(default_factory=deque)   # entries: (triggered|None, strength)
    active: Diagnostic | None = None
    last: RuleResult | None = None      # most recent triggering result
    latest: RuleResult | None = None    # most recent evaluated result (triggered or not)

    def resize(self, n: int) -> None:
        if self.window.maxlen != n:
            self.window = deque(self.window, maxlen=n)

    def stats(self, last_n: int | None = None) -> tuple[int, int, float]:
        """Stats over the most recent ``last_n`` *evaluable* ticks (ticks where the
        rule could not be evaluated - e.g. silence between drum hits - are skipped,
        so persistence is measured over signal time, bounded by the window length)."""
        return self.stats2(last_n or len(self.window), 0)[0]

    def stats2(self, n1: int, n2: int) -> tuple[tuple[int, int, float], tuple[int, int, float]]:
        """Single backwards pass computing stats for the last ``n1`` and ``n2`` evaluable ticks."""
        e1 = h1 = e2 = h2 = 0
        s1 = 0.0
        stop = max(n1, n2)
        for trig, strength in reversed(self.window):
            if trig is None:
                continue
            if e1 < n1:
                e1 += 1
                if trig:
                    h1 += 1
                    s1 += strength
            if e2 < n2:
                e2 += 1
                if trig:
                    h2 += 1
            if e1 >= stop and e2 >= stop or (e1 >= n1 and e2 >= n2):
                break
        return (e1, h1, s1 / h1 if h1 else 0.0), (e2, h2, 0.0)


class DiagnosticEngine:
    def __init__(self, settings: AnalysisSettings, rate_hz: float):
        self.settings = settings
        self.rate_hz = rate_hz
        self.trackers: dict[tuple[int, str], _Tracker] = {}
        self.tick = 0

    def update_settings(self, settings: AnalysisSettings, rate_hz: float) -> None:
        self.settings, self.rate_hz = settings, rate_hz

    def reset_channel(self, channel_index: int, rule: str | None = None) -> None:
        """Forget accumulated evidence (after an operator adjustment / re-analysis)."""
        for (ch, key), tr in self.trackers.items():
            if ch == channel_index and (rule is None or key == rule):
                tr.window.clear()

    def drop_channels_except(self, keep: set[int]) -> list[Diagnostic]:
        """Unselected channels must not keep diagnostics. Returns cleared ones."""
        cleared = []
        for key in list(self.trackers):
            if key[0] not in keep:
                tr = self.trackers.pop(key)
                if tr.active is not None:
                    tr.active.status = "cleared"
                    tr.active.cleared_at = now()
                    cleared.append(tr.active)
        return cleared

    def _window_len(self, spec: RuleSpec) -> int:
        return max(spec.min_hits, int(round(self.settings.diagnostic_persistence_s * spec.persistence_factor * self.rate_hz)))

    def _clear_len(self, spec: RuleSpec) -> int:
        return max(spec.min_hits, int(round(self.settings.clear_time_s * max(spec.persistence_factor, 0.5) * self.rate_hz)))

    def evaluate(self, channel_index: int, channel_name: str, profile_key: str,
                 analysis: ChannelAnalysis) -> tuple[list[Diagnostic], list[Diagnostic]]:
        """Run every rule enabled for the profile. Returns (active diagnostics, newly cleared)."""
        profile = get_profile(profile_key)
        ctx = RuleContext(self.settings, self.rate_hz)
        t = now()
        cleared: list[Diagnostic] = []
        for key, spec in RULES.items():
            if key not in profile.rules:
                continue
            tr = self.trackers.get((channel_index, key))
            if tr is None:
                tr = self.trackers[(channel_index, key)] = _Tracker(spec)
            wlen = self._window_len(spec)
            tr.resize(3 * max(wlen, self._clear_len(spec)))  # up to 3x real time to gather evidence
            try:
                res = spec.func(analysis, profile, ctx)
            except Exception:  # noqa: BLE001 - a broken rule must not stop analysis
                log.exception("diagnostic rule failed", extra={"rule": key, "channel": channel_index + 1})
                res = None
            if res is None:
                tr.window.append((None, 0.0))
            else:
                tr.window.append((bool(res.triggered), float(res.strength)))
                tr.latest = res
                if res.triggered:
                    tr.last = res  # most recent *triggering* evidence
            self._advance(tr, channel_index, channel_name, profile, wlen, t, cleared)
        active = [tr.active for (ch, _), tr in self.trackers.items() if ch == channel_index and tr.active]
        self._resolve_conflicts(active)
        for d in active:
            d.unsuppressed_s = 0.0 if d.suppressed_by else d.unsuppressed_s + 1.0 / self.rate_hz
        return active, cleared

    def _advance(self, tr: _Tracker, ch: int, name: str, profile: Profile, wlen: int, t: float,
                 cleared: list[Diagnostic]) -> None:
        spec = tr.spec
        clen = self._clear_len(spec)
        (n_eval, n_hits, mean_s), (c_eval, c_hits, _) = tr.stats2(wlen, clen)
        if tr.active is None:
            if n_eval >= max(spec.min_hits, int(0.6 * wlen)) and n_hits / max(n_eval, 1) >= CONFIRM_RATIO and tr.last:
                tr.active = self._make(tr, ch, name, profile, wlen, t)
                log.info("diagnostic raised", extra={"channel": ch + 1, "category": spec.key,
                                                     "severity": tr.active.severity,
                                                     "confidence": tr.active.confidence})
            return
        # active: refresh or clear. Clearing needs a reasonably full clear window, so that a
        # freshly reset window (after "applied" / re-analysis) cannot fake a resolution.
        if c_eval >= max(spec.min_hits, int(0.6 * clen)) and c_hits / c_eval < CLEAR_RATIO:
            d = tr.active
            d.status, d.cleared_at = "cleared", t
            cleared.append(d)
            tr.active = None
            log.info("diagnostic cleared", extra={"channel": ch + 1, "category": spec.key})
            return
        tr.active.age_s += 1.0 / self.rate_hz
        if n_hits and tr.last is not None and tr.last.triggered:
            self._refresh(tr, profile, wlen, t)

    def _confidence(self, tr: _Tracker, profile: Profile, wlen: int) -> tuple[float, dict[str, Any]]:
        n_eval, n_hits, mean_s = tr.stats(wlen)
        evidence = 0.4 + 0.6 * mean_s
        persistence = n_hits / n_eval if n_eval else 0.0
        weight = 1.0 if tr.spec.key in profile.priority else 0.75
        sufficiency = min(1.0, n_eval / wlen) if wlen else 0.0
        context = weight * sufficiency
        c = W_EVIDENCE * evidence + W_PERSISTENCE * persistence + W_CONTEXT * context
        return round(float(c), 3), {
            "evidence": round(evidence, 3), "persistence": round(persistence, 3), "context": round(context, 3),
            "profile_weight": weight, "data_sufficiency": round(sufficiency, 3),
            "evaluable_ticks": n_eval, "triggered_ticks": n_hits, "window_ticks": wlen,
            "formula": "0.5*evidence + 0.3*persistence + 0.2*context",
        }

    def _make(self, tr: _Tracker, ch: int, name: str, profile: Profile, wlen: int, t: float) -> Diagnostic:
        r = tr.last
        conf, breakdown = self._confidence(tr, profile, wlen)
        return Diagnostic(
            id=uuid.uuid4().hex[:12], channel=ch + 1, channel_name=name, profile=profile.key,
            category=tr.spec.key, title=tr.spec.title, group=tr.spec.group, severity=r.severity,
            confidence=conf, confidence_breakdown=breakdown, first_seen=t, last_seen=t,
            evidence=r.evidence, explanation=r.explanation, target_hz=r.target_hz, q_hint=r.q_hint,
            magnitude=r.magnitude, unit=r.unit,
        )

    def _refresh(self, tr: _Tracker, profile: Profile, wlen: int, t: float) -> None:
        d, r = tr.active, tr.last
        d.confidence, d.confidence_breakdown = self._confidence(tr, profile, wlen)
        d.severity, d.evidence, d.explanation = r.severity, r.evidence, r.explanation
        d.target_hz, d.q_hint, d.magnitude, d.last_seen = r.target_hz, r.q_hint, r.magnitude, t

    # ------------------------------------------------------------------ conflicts
    @staticmethod
    def _octaves(f1: float | None, f2: float | None) -> float:
        if not f1 or not f2:
            return 99.0
        return abs(float(np.log2(f1 / f2)))

    def _resolve_conflicts(self, diags: list[Diagnostic]) -> None:
        """Mark diagnostics that should not produce a recommendation right now.
        Suppressed diagnostics stay visible (with the reason) for transparency."""
        prev_winners = {d.category for d in diags if d.suppressed_by is None and d.last_seen != d.first_seen}
        for d in diags:
            d.suppressed_by = None
        by = {d.category: d for d in diags}
        if "clipping" in by:
            for k in ("weak_signal", "low_headroom"):
                if k in by:
                    by[k].suppressed_by = "clipping"
        hum = by.get("hum")
        if hum and hum.target_hz:
            # hum harmonics show up as low-frequency energy; fix the hum first
            harmonics = [hum.target_hz * k for k in range(1, 6)]
            for k in ("rumble", "boomy", "muddy", "boxy", "low_mid_excess", "spectral_concentration"):
                if k in by and (k == "rumble" or any(self._octaves(by[k].target_hz, h) <= 1 / 6 for h in harmonics)):
                    by[k].suppressed_by = "hum"
        fb = by.get("feedback_risk")
        if fb:
            for k in ("harsh", "weak_presence", "boxy", "muddy", "spectral_concentration"):
                if k in by and self._octaves(by[k].target_hz, fb.target_hz) <= 1 / 3:
                    by[k].suppressed_by = "feedback_risk"
        # Overlapping low-mid diagnostics usually describe the *same* bump. The one whose
        # characteristic range is centred nearest the bump wins (stable, unlike comparing
        # magnitudes that fluctuate by a dB or two); the incumbent gets a 0.2-octave bonus.
        lowmid = [by[k] for k in ("boomy", "muddy", "boxy", "low_mid_excess") if k in by and not by[k].suppressed_by]
        for i, a in enumerate(lowmid):
            for b in lowmid[i + 1:]:
                if a.suppressed_by or b.suppressed_by or self._octaves(a.target_hz, b.target_hz) > 0.75:
                    continue
                bump = (a if a.magnitude >= b.magnitude else b).target_hz

                min_c = self.settings.min_confidence

                def score(d: Diagnostic) -> float:
                    return (self._octaves(bump, _LOWMID_CENTER[d.category])
                            + (0.3 if d.category == "low_mid_excess" else 0.0)
                            - (0.2 if d.category in prev_winners else 0.0)
                            + (2.0 if d.confidence < min_c else 0.0))  # prefer an actionable diagnosis

                win, lose = (a, b) if score(a) <= score(b) else (b, a)
                lose.suppressed_by = win.category
        wp = by.get("weak_presence")
        if wp and not wp.suppressed_by:
            # "cut before boost": address harshness / low-mid excess before suggesting a presence boost
            for k in ("harsh", "muddy", "boxy", "low_mid_excess", "boomy"):
                if k in by and not by[k].suppressed_by:
                    wp.suppressed_by = k
                    break
        if "spectral_concentration" in by and not by["spectral_concentration"].suppressed_by:
            sc = by["spectral_concentration"]
            for k in ("boomy", "muddy", "boxy", "low_mid_excess", "harsh", "rumble"):
                if k in by and self._octaves(by[k].target_hz, sc.target_hz) <= 0.5:
                    sc.suppressed_by = k
                    break

    def latest_result(self, channel_index: int, key: str) -> tuple[RuleResult | None, bool]:
        """(latest evaluated result, diagnostic currently active) for one rule."""
        tr = self.trackers.get((channel_index, key))
        if tr is None:
            return None, False
        return tr.latest, tr.active is not None

    def active_for(self, channel_index: int | None = None) -> list[Diagnostic]:
        out = [tr.active for (ch, _), tr in self.trackers.items()
               if tr.active and (channel_index is None or ch == channel_index)]
        out.sort(key=lambda d: (SEVERITY_RANK[d.severity], d.confidence), reverse=True)
        return out
