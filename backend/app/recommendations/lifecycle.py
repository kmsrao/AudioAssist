"""Recommendation lifecycle.

States: new -> pending -> applied -> resolved
                     \\-> dismissed       \\-> (persists: stays applied)
        new/pending -> expired (condition no longer detected / superseded / channel deselected)

* One live recommendation per (channel, category) - updates refresh it in place
  (no duplicates).
* Cooldown after dismissal (``recommendation_cooldown_s``) and a short one
  after expiry/resolution, so a flapping condition cannot spam the operator.
* After "applied", the analyzer state for the channel is reset and the result
  is verified after ``verification_window_s``: cleared -> resolved; still
  present -> stays applied with a "persists"/"improved" verdict.
* Nothing here touches audio or the mixer.
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import AnalysisSettings
from app.diagnostics.engine import SEVERITY_RANK, Diagnostic
from app.logging.setup import get_logger
from app.recommendations.builder import recommendation_fields, summary_line
from app.utils.timeutil import now

log = get_logger("recommendations")

ACTIVE_STATES = ("new", "pending", "applied")
TERMINAL_STATES = ("dismissed", "resolved", "expired")
SHORT_COOLDOWN_S = 20.0
# Tonal/noise diagnostics must stay *continuously* unsuppressed this long (analysed
# time) before a recommendation
# is shown, so a related root cause (e.g. hum behind "boominess") can be confirmed
# first. Clipping and feedback are urgent and are not delayed.
SETTLE_S = 2.5
URGENT_GROUPS = ("dynamics", "feedback")


@dataclass
class Recommendation:
    id: str
    diagnostic_id: str
    channel: int
    channel_name: str
    profile: str
    category: str
    fields: dict[str, Any]
    status: str = "new"
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    history: list[dict[str, Any]] = field(default_factory=list)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    applied_at: float | None = None
    closed_at: float | None = None
    note: str | None = None
    # lifecycle timing in *analysed* time (see RecommendationManager.clock); not serialised
    _created_clock: float = 0.0
    _due_clock: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "fields" and not k.startswith("_")}
        d.update(self.fields)
        d["summary"] = summary_line(self.fields)
        d["timestamp"] = self.updated_at
        return d


@dataclass
class _Cooldown:
    until: float
    reason: str


class RecommendationManager:
    def __init__(self, settings: AnalysisSettings,
                 persist: Callable[[Recommendation], None] | None = None,
                 on_event: Callable[[Recommendation, str, str, str | None], None] | None = None,
                 snapshot: Callable[[int, str], dict[str, Any]] | None = None,
                 reset_channel: Callable[[int, str | None], None] | None = None,
                 clock: Callable[[], float] | None = None):
        """``clock`` measures lifecycle durations (new->pending, verification window).
        The service passes *analysed audio time*, which equals wall time in live use and
        stays correct when tests drive the analysis faster than real time."""
        self.settings = settings
        self.clock = clock or (lambda: now())
        self._persist = persist or (lambda r: None)
        self._on_event = on_event or (lambda r, a, b, n: None)
        self._snapshot = snapshot or (lambda ch, cat: {})
        self._reset_channel = reset_channel or (lambda ch, cat: None)
        self.active: dict[tuple[int, str], Recommendation] = {}
        self.by_id: dict[str, Recommendation] = {}
        self.recent_closed: list[Recommendation] = []
        self.cooldowns: dict[tuple[int, str], _Cooldown] = {}
        self.version = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _bump(self) -> None:
        self.version += 1

    def _transition(self, rec: Recommendation, status: str, note: str | None = None) -> None:
        old = rec.status
        if old == status:
            return
        rec.status = status
        rec.updated_at = now()
        rec.history.append({"from": old, "to": status, "at": rec.updated_at, "note": note})
        if status in TERMINAL_STATES:
            rec.closed_at = rec.updated_at
            key = (rec.channel - 1, rec.category)
            if self.active.get(key) is rec:
                del self.active[key]
            self.recent_closed.insert(0, rec)
            del self.recent_closed[20:]
        self._on_event(rec, old, status, note)
        self._persist(rec)
        self._bump()
        log.info("recommendation status", extra={"id": rec.id, "channel": rec.channel, "category": rec.category,
                                                 "from": old, "to": status, "note": note})

    def _in_cooldown(self, key: tuple[int, str], t: float) -> bool:
        cd = self.cooldowns.get(key)
        if cd is None:
            return False
        if t >= cd.until:
            del self.cooldowns[key]
            return False
        return True

    # ------------------------------------------------------------------ analysis-driven updates
    def sync_channel(self, ch_index: int, diagnostics: list[Diagnostic], cleared: list[Diagnostic]) -> None:
        with self._lock:
            t = now()
            s = self.settings
            for d in cleared:
                rec = self.active.get((ch_index, d.category))
                if rec is None:
                    continue
                if rec.status == "applied":
                    self._verify(rec, resolved=True)
                else:
                    self.cooldowns[(ch_index, d.category)] = _Cooldown(t + SHORT_COOLDOWN_S, "expired")
                    self._transition(rec, "expired", "condition no longer detected")
            for d in diagnostics:
                key = (ch_index, d.category)
                rec = self.active.get(key)
                if d.suppressed_by:
                    if rec is not None and rec.status in ("new", "pending"):
                        self._transition(rec, "expired", f"superseded by {d.suppressed_by}")
                        self.cooldowns[key] = _Cooldown(t + SHORT_COOLDOWN_S, "superseded")
                    continue
                if rec is None:
                    if d.confidence < s.min_confidence or self._in_cooldown(key, t):
                        continue
                    if d.group not in URGENT_GROUPS and d.unsuppressed_s < SETTLE_S:
                        continue
                    rec = Recommendation(id=uuid.uuid4().hex[:12], diagnostic_id=d.id, channel=d.channel,
                                         channel_name=d.channel_name, profile=d.profile, category=d.category,
                                         fields=recommendation_fields(d, s), created_at=t, updated_at=t,
                                         _created_clock=self.clock())
                    rec.history.append({"from": None, "to": "new", "at": rec.created_at, "note": None})
                    d.recommended_action = summary_line(rec.fields)
                    self.active[key] = rec
                    self.by_id[rec.id] = rec
                    self._persist(rec)
                    self._on_event(rec, "", "new", None)
                    self._bump()
                    log.info("recommendation generated", extra={"id": rec.id, "channel": rec.channel,
                                                                "category": rec.category,
                                                                "summary": summary_line(rec.fields),
                                                                "confidence": d.confidence})
                elif rec.status in ("new", "pending"):
                    # refresh in place - the operator has not acted yet
                    new_fields = recommendation_fields(d, s)
                    if new_fields != rec.fields:
                        rec.fields = new_fields
                        rec.updated_at = t
                        rec.diagnostic_id = d.id
                        self._bump()
                    d.recommended_action = summary_line(rec.fields)
                else:  # applied: keep what the operator acted on; record latest measurement
                    rec.fields["latest_measurement"] = {"value": round(d.magnitude, 2), "unit": d.unit,
                                                        "confidence": d.confidence}
                    d.recommended_action = summary_line(rec.fields)

    def tick(self) -> None:
        with self._lock:
            c = self.clock()
            for rec in list(self.active.values()):
                if rec.status == "new" and c - rec._created_clock >= self.settings.new_to_pending_s:
                    self._transition(rec, "pending", "awaiting operator action")
                if rec.status == "applied" and rec.verification and rec.verification.get("state") == "pending":
                    if rec._due_clock is not None and c >= rec._due_clock:
                        self._verify(rec, resolved=False)

    def _verify(self, rec: Recommendation, resolved: bool) -> None:
        after = self._snapshot(rec.channel - 1, rec.category)
        rec.after = after
        before_m = (rec.before or {}).get("metric")
        after_m = after.get("metric")
        delta = None
        if before_m is not None and after_m is not None:
            delta = round(after_m - before_m, 2)
        if resolved or not after.get("diagnostic_active", True):
            rec.verification = {"state": "resolved", "checked_at": now(), "metric_before": before_m,
                                "metric_after": after_m, "delta": delta,
                                "note": "The condition is no longer detected after the adjustment."}
            self._transition(rec, "resolved", "condition cleared after adjustment")
            self.cooldowns[(rec.channel - 1, rec.category)] = _Cooldown(now() + SHORT_COOLDOWN_S, "resolved")
            return
        # conservative: both an absolute (>= 2 units) and a relative (>= 25 %) reduction
        improved = delta is not None and bool(before_m) and (abs(after_m) <= abs(before_m) - 2.0
                                                             and abs(after_m) <= 0.75 * abs(before_m))
        rec.verification = {
            "state": "improved" if improved else "persists", "checked_at": now(), "metric_before": before_m,
            "metric_after": after_m, "delta": delta,
            "note": ("Measurably improved but the condition is still detected - consider a further small adjustment."
                     if improved else "The condition is still detected after the adjustment."),
        }
        rec.updated_at = now()
        self._persist(rec)
        self._on_event(rec, "applied", "applied", f"verification: {rec.verification['state']}")
        self._bump()

    # ------------------------------------------------------------------ operator actions
    def mark_applied(self, rec_id: str, note: str | None = None) -> Recommendation:
        """Record that the operator made the adjustment. Allowed again on an
        already-applied recommendation (a further adjustment after a
        "persists" verdict): a new before-snapshot and verification start."""
        with self._lock:
            rec = self._get_active(rec_id)
            rec.before = self._snapshot(rec.channel - 1, rec.category)
            rec.applied_at = now()
            rec.note = note or rec.note
            rec.verification = {"state": "pending", "due_at": rec.applied_at + self.settings.verification_window_s}
            rec._due_clock = self.clock() + self.settings.verification_window_s
            self._transition(rec, "applied", note or "operator applied adjustment")
            self._reset_channel(rec.channel - 1, rec.category)
            return rec

    def dismiss(self, rec_id: str, note: str | None = None) -> Recommendation:
        with self._lock:
            rec = self._get_active(rec_id)
            rec.note = note or rec.note
            self.cooldowns[(rec.channel - 1, rec.category)] = _Cooldown(
                now() + self.settings.recommendation_cooldown_s, "dismissed")
            self._transition(rec, "dismissed", note or "dismissed by operator")
            return rec

    def reanalyze(self, rec_id: str) -> Recommendation:
        """Restart evidence collection for the channel and schedule a fresh
        before/after comparison."""
        with self._lock:
            rec = self.by_id.get(rec_id)
            if rec is None:
                raise KeyError(rec_id)
            snap = self._snapshot(rec.channel - 1, rec.category)
            if rec.status == "applied":
                rec.verification = {"state": "pending", "due_at": now() + self.settings.verification_window_s,
                                    "requested": "re-analysis"}
                rec._due_clock = self.clock() + self.settings.verification_window_s
            rec.after = snap
            rec.updated_at = now()
            self._reset_channel(rec.channel - 1, None)
            self._on_event(rec, rec.status, rec.status, "re-analysis requested")
            self._persist(rec)
            self._bump()
            return rec

    def expire_channels_except(self, keep: set[int], reason: str = "channel deselected") -> None:
        with self._lock:
            for (ch, _), rec in list(self.active.items()):
                if ch not in keep:
                    self._transition(rec, "expired", reason)

    def _get_active(self, rec_id: str) -> Recommendation:
        rec = self.by_id.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        if rec.status not in ACTIVE_STATES:
            raise ValueError(f"recommendation is {rec.status}")
        return rec

    # ------------------------------------------------------------------ queries
    def list_active(self) -> list[Recommendation]:
        with self._lock:
            recs = list(self.active.values())
        # actionable first (applied ones last), then by severity, then confidence
        recs.sort(key=lambda r: (r.status == "applied", -SEVERITY_RANK.get(r.fields.get("severity", "low"), 0),
                                 -r.fields.get("confidence", 0.0)))
        return recs

    def get(self, rec_id: str) -> Recommendation | None:
        return self.by_id.get(rec_id)
