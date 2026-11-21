"""Recommendation builder and lifecycle."""
import pytest

from app.config.settings import AnalysisSettings
from app.diagnostics.engine import Diagnostic
from app.recommendations import lifecycle as lc
from app.recommendations.builder import build_action, recommendation_fields, round_freq, summary_line
from app.recommendations.lifecycle import RecommendationManager


def diag(category="muddy", magnitude=10.0, target=320.0, q=1.2, confidence=0.8, severity="medium",
         group="tonal", evidence=None, channel=1, first_seen=0.0, suppressed_by=None, age_s=10.0) -> Diagnostic:  # noqa: E501
    return Diagnostic(id=f"d-{category}", channel=channel, channel_name="Pastor Mic", profile="speech",
                      category=category, title=category.title(), group=group, severity=severity,
                      confidence=confidence, confidence_breakdown={}, first_seen=first_seen, last_seen=first_seen,
                      evidence=evidence or {}, explanation="because", target_hz=target, q_hint=q,
                      magnitude=magnitude, unit="dB", suppressed_by=suppressed_by, age_s=age_s,
                      unsuppressed_s=age_s)


S = AnalysisSettings()


# ------------------------------------------------------------------ builder
def test_eq_cut_is_conservative_and_limited():
    a = build_action(diag(magnitude=10.0), S)
    assert a.filter_type == "bell" and a.gain_db == -5.0 and a.q == 1.2 and a.target_hz == 320.0
    big = build_action(diag(magnitude=40.0), S)
    assert big.gain_db == -S.max_eq_cut_db
    small = build_action(diag(magnitude=1.0), S)
    assert small.gain_db == -2.0


def test_cut_respects_configured_max_cut():
    s = AnalysisSettings(max_eq_cut_db=3.0)
    assert build_action(diag(magnitude=20.0), s).gain_db == -3.0


def test_presence_boost_is_small_and_capped():
    a = build_action(diag("weak_presence", magnitude=12.0, target=1800.0), S)
    assert 0 < a.gain_db <= S.max_eq_boost_db
    s = AnalysisSettings(max_eq_boost_db=1.0)
    assert build_action(diag("weak_presence", magnitude=12.0, target=1800.0), s).gain_db == 1.0


def test_targets_are_dynamic_not_hardcoded():
    a1 = build_action(diag(target=260.0), S)
    a2 = build_action(diag(target=455.0), S)
    assert a1.target_hz != a2.target_hz


def test_hpf_recommendation():
    a = build_action(diag("rumble", target=100.0, group="noise", evidence={"suggested_hpf_hz": 95.0}), S)
    assert a.filter_type == "high_pass" and a.hpf_hz == 95.0 and a.gain_db is None


def test_clipping_recommends_gain_reduction():
    a = build_action(diag("clipping", group="dynamics", evidence={"clip_events_last_5s": 12}), S)
    assert a.filter_type == "gain" and a.gain_change_db == -6.0


def test_weak_signal_gain_respects_headroom():
    ev = {"profile_nominal_rms_dbfs": -24, "signal_p90_rms_dbfs": -50, "peak_hold_dbfs": -14}
    a = build_action(diag("weak_signal", group="dynamics", evidence=ev), S)
    assert a.gain_change_db == 8.0  # limited by peaks: -14 + 8 = -6 dBFS


def test_feedback_notch_is_narrow():
    a = build_action(diag("feedback_risk", target=2809.8, q=10.0, severity="high", group="feedback"), S)
    assert a.filter_type == "notch" and a.q >= 8 and a.target_hz == 2810.0 and a.gain_db == -3.0


def test_hum_prefers_source_fix_with_eq_fallback():
    a = build_action(diag("hum", target=60.0, group="noise", evidence={"mains_hz": 60.0}), S)
    assert a.control.startswith("Source") and a.fallback["filter_type"] == "notch"


def test_fields_and_summary():
    f = recommendation_fields(diag(), S)
    for k in ("control", "target_hz", "gain_db", "q", "explanation", "expected_improvement",
              "supporting_analysis", "confidence", "severity", "issue"):
        assert k in f
    assert "320 Hz" in summary_line(f)
    assert round_freq(1234.0) == 1230.0 and round_freq(1234.0, precise=True) == 1235.0 and round_freq(57.4) == 57.0


# ------------------------------------------------------------------ lifecycle
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(lc, "now", c)
    return c


def make_mgr(**over):
    snaps = []
    resets = []
    state = {"active": True, "metric": 10.0}

    def snapshot(ch, cat):
        snaps.append((ch, cat))
        return {"metric": state["metric"], "diagnostic_active": state["active"]}

    mgr = RecommendationManager(AnalysisSettings(**over), snapshot=snapshot, reset_channel=lambda c, k: resets.append(c))
    return mgr, state, resets


def test_new_recommendation_after_settle_and_no_duplicates(clock):
    mgr, _, _ = make_mgr()
    d = diag(first_seen=clock.t, age_s=0.5)
    mgr.sync_channel(0, [d], [])
    assert not mgr.active  # settling (measured in analysed time)
    d.unsuppressed_s = 3.0
    mgr.sync_channel(0, [d], [])
    mgr.sync_channel(0, [d], [])
    assert len(mgr.active) == 1
    rec = mgr.list_active()[0]
    assert rec.status == "new" and rec.channel == 1 and rec.channel_name == "Pastor Mic"


def test_urgent_issues_skip_settle(clock):
    mgr, _, _ = make_mgr()
    mgr.sync_channel(0, [diag("clipping", group="dynamics", first_seen=clock.t)], [])
    assert len(mgr.active) == 1


def test_low_confidence_not_recommended(clock):
    mgr, _, _ = make_mgr(min_confidence=0.7)
    mgr.sync_channel(0, [diag(confidence=0.6, first_seen=0)], [])
    assert not mgr.active


def test_new_becomes_pending(clock):
    mgr, _, _ = make_mgr(new_to_pending_s=10)
    mgr.sync_channel(0, [diag(group="dynamics", category="clipping")], [])
    clock.t += 11
    mgr.tick()
    assert mgr.list_active()[0].status == "pending"


def test_apply_then_resolved_when_condition_clears(clock):
    mgr, state, resets = make_mgr()
    d = diag(group="dynamics", category="clipping")
    mgr.sync_channel(0, [d], [])
    rec = mgr.list_active()[0]
    mgr.mark_applied(rec.id, "trim -6")
    assert rec.status == "applied" and rec.before["metric"] == 10.0 and resets == [0]
    state["active"], state["metric"] = False, 0.0
    d.status = "cleared"
    mgr.sync_channel(0, [], [d])
    assert rec.status == "resolved" and rec.verification["state"] == "resolved"
    assert not mgr.active and mgr.recent_closed[0] is rec


def test_apply_then_persists_after_verification_window(clock):
    mgr, state, _ = make_mgr(verification_window_s=8)
    d = diag(group="dynamics", category="clipping")
    mgr.sync_channel(0, [d], [])
    rec = mgr.list_active()[0]
    mgr.mark_applied(rec.id)
    clock.t += 9
    state["metric"] = 9.5
    mgr.tick()
    assert rec.status == "applied" and rec.verification["state"] == "persists"
    assert rec.after["metric"] == 9.5


def test_apply_improved_verdict(clock):
    mgr, state, _ = make_mgr(verification_window_s=8)
    mgr.sync_channel(0, [diag(group="dynamics", category="clipping")], [])
    rec = mgr.list_active()[0]
    mgr.mark_applied(rec.id)
    clock.t += 9
    state["metric"] = 5.0
    mgr.tick()
    assert rec.verification["state"] == "improved" and rec.verification["delta"] == -5.0


def test_dismiss_starts_cooldown(clock):
    mgr, _, _ = make_mgr(recommendation_cooldown_s=120)
    d = diag(group="dynamics", category="clipping")
    mgr.sync_channel(0, [d], [])
    rec = mgr.list_active()[0]
    mgr.dismiss(rec.id, "intentional distortion")
    assert rec.status == "dismissed"
    mgr.sync_channel(0, [d], [])
    assert not mgr.active  # cooldown
    clock.t += 121
    mgr.sync_channel(0, [d], [])
    assert len(mgr.active) == 1


def test_expired_when_condition_clears_before_action(clock):
    mgr, _, _ = make_mgr()
    d = diag(group="dynamics", category="clipping")
    mgr.sync_channel(0, [d], [])
    rec = mgr.list_active()[0]
    mgr.sync_channel(0, [], [d])
    assert rec.status == "expired" and rec.history[-1]["note"] == "condition no longer detected"


def test_suppressed_diagnostic_expires_its_recommendation(clock):
    mgr, _, _ = make_mgr()
    d = diag(first_seen=0)
    mgr.sync_channel(0, [d], [])
    rec = mgr.list_active()[0]
    d.suppressed_by = "hum"
    mgr.sync_channel(0, [d], [])
    assert rec.status == "expired" and "hum" in rec.history[-1]["note"]


def test_cannot_act_on_closed_recommendation(clock):
    mgr, _, _ = make_mgr()
    mgr.sync_channel(0, [diag(group="dynamics", category="clipping")], [])
    rec = mgr.list_active()[0]
    mgr.dismiss(rec.id)
    with pytest.raises(ValueError):
        mgr.mark_applied(rec.id)
    with pytest.raises(KeyError):
        mgr.dismiss("nope")


def test_deselected_channel_recommendations_expire(clock):
    mgr, _, _ = make_mgr()
    mgr.sync_channel(0, [diag(group="dynamics", category="clipping")], [])
    mgr.sync_channel(1, [diag(group="dynamics", category="clipping", channel=2)], [])
    mgr.expire_channels_except({1})
    assert [r.channel for r in mgr.list_active()] == [2]


def test_ranking_by_severity_then_confidence(clock):
    mgr, _, _ = make_mgr()
    mgr.sync_channel(0, [diag("clipping", group="dynamics", severity="critical", confidence=0.7)], [])
    mgr.sync_channel(1, [diag("feedback_risk", group="feedback", severity="high", confidence=0.95, channel=2)], [])
    mgr.sync_channel(2, [diag("low_headroom", group="dynamics", severity="high", confidence=0.6, channel=3)], [])
    order = [r.category for r in mgr.list_active()]
    assert order == ["clipping", "feedback_risk", "low_headroom"]
