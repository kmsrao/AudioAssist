"""Diagnostic rules + engine on synthetic signals (no hardware)."""
import numpy as np
import pytest

from tests.synth import signals as ms
from tests.synth.scenarios import _guitar_feedback, _keyboard
from app.config.settings import AnalysisSettings
from app.diagnostics.engine import W_CONTEXT, W_EVIDENCE, W_PERSISTENCE, confidence_label
from app.diagnostics.profiles import PROFILES, get_profile

from .conftest import FS, first_time, run_offline

TONAL = {"muddy", "boxy", "boomy", "low_mid_excess", "harsh", "sibilance", "weak_presence", "spectral_concentration"}


def speech(seconds, seed=11, amp=0.6):
    return ms.render(ms.SpeechLike(FS, amp=amp, f0=120, seed=seed), seconds, FS)


def test_clean_speech_has_no_tonal_diagnostics():
    eng, a, tl = run_offline(speech(25), "speech")
    raised = set().union(*(c for _, c in tl))
    assert not (raised & TONAL), raised
    assert "feedback_risk" not in raised and "clipping" not in raised


def test_muddy_speech_is_detected_in_low_mid_range():
    x = ms.render(ms.colored(ms.SpeechLike(FS, amp=0.5, f0=115, seed=11), FS, 300, 12.0, 1.0), 25, FS)
    eng, a, tl = run_offline(x, "speech")
    d = next(d for d in eng.active_for(0) if d.category == "muddy")
    assert 200 <= d.target_hz <= 500
    assert d.evidence["peak_excess_db"] >= 7
    assert d.confidence >= 0.55 and d.suppressed_by is None


def test_harsh_vocal_detected():
    x = ms.render(ms.colored(ms.SpeechLike(FS, amp=0.5, f0=300, seed=5, sustained=True), FS, 4000, 12.0, 1.2), 20, FS)
    eng, _, _ = run_offline(x, "lead_vocal")
    d = next(d for d in eng.active_for(0) if d.category == "harsh")
    assert 2500 <= d.target_hz <= 5000


def test_sibilance_detected_when_hf_bursts_are_boosted():
    x = ms.render(ms.colored(ms.SpeechLike(FS, amp=0.5, f0=120, seed=5), FS, 7000, 12.0, 1.5), 30, FS)
    eng, _, tl = run_offline(x, "speech")
    assert first_time(tl, "sibilance") is not None


def test_diagnosis_requires_persistence():
    """Nothing tonal may be raised before the persistence window has elapsed."""
    x = ms.render(ms.colored(ms.SpeechLike(FS, amp=0.5, f0=115, seed=11), FS, 300, 12.0, 1.0), 20, FS)
    _, _, tl = run_offline(x, "speech")
    t = first_time(tl, "muddy")
    s = AnalysisSettings()
    assert t is not None and t >= s.diagnostic_persistence_s


def test_clipping_detected_quickly():
    x = ms.render(ms.HardClip(ms.Percussive(FS, "kick", 0.6, 0.5, seed=3), drive=2.5), 5, FS)
    eng, _, tl = run_offline(x, "kick")
    t = first_time(tl, "clipping")
    assert t is not None and t <= 2.0
    d = next(d for d in eng.active_for(0) if d.category == "clipping")
    assert d.severity in ("high", "critical")


def test_rumble_detected_with_hpf_suggestion_in_profile_range():
    x = ms.render(ms.Sum(ms.Percussive(FS, "snare", 0.35, 1.0, seed=2), ms.Rumble(FS, 0.12, 40.0, seed=2)), 15, FS)
    eng, _, _ = run_offline(x, "snare")
    d = next(d for d in eng.active_for(0) if d.category == "rumble")
    lo, hi = PROFILES["snare"].hpf_range
    assert lo <= d.evidence["suggested_hpf_hz"] <= hi


def test_hum_detected_and_suppresses_its_symptoms():
    voices = [ms.SpeechLike(FS, amp=0.15, f0=f, seed=20 + i, sustained=True, pause_prob=0.05)
              for i, f in enumerate((200, 250, 300))]
    x = ms.render(ms.Sum(*voices, ms.Hum(FS, 60.0, amp=0.006)), 20, FS)
    eng, _, _ = run_offline(x, "choir")
    by = {d.category: d for d in eng.active_for(0)}
    assert "hum" in by and by["hum"].target_hz == 60.0
    for k in ("rumble", "boomy"):
        if k in by:
            assert by[k].suppressed_by == "hum"


def test_feedback_tone_detected_on_guitar():
    x = ms.render(_guitar_feedback(FS, 1004), 20, FS)
    eng, _, tl = run_offline(x, "acoustic_guitar")
    d = next(d for d in eng.active_for(0) if d.category == "feedback_risk")
    assert d.target_hz == pytest.approx(2810, abs=6)
    assert d.evidence["harmonic_series"] is False
    assert 8 <= d.q_hint <= 16


def test_single_transient_tone_is_not_feedback():
    """A 0.5 s tone burst (e.g. a note or a beep) must not be classified as feedback."""
    x = ms.render(ms.SpeechLike(FS, amp=0.4, seed=3), 12, FS)
    burst = 0.2 * np.sin(2 * np.pi * 2500 * np.arange(int(0.5 * FS)) / FS)
    x[int(4 * FS):int(4.5 * FS)] += burst
    _, _, tl = run_offline(x, "speech")
    assert first_time(tl, "feedback_risk") is None


def test_keyboard_sustained_partials_not_flagged_as_feedback():
    x = ms.render(_keyboard(FS, 1003), 30, FS)
    _, _, tl = run_offline(x, "keyboard")
    raised = set().union(*(c for _, c in tl))
    assert "feedback_risk" not in raised
    assert not (raised & TONAL), raised


def test_weak_signal_detected():
    x = ms.render(ms.Plucked(FS, [41.2, 55.0, 73.4], amp=0.006, interval_s=0.5, decay_s=0.8, seed=2), 15, FS)
    eng, _, _ = run_offline(x, "bass")
    d = next(d for d in eng.active_for(0) if d.category == "weak_signal")
    assert d.evidence["signal_p90_rms_dbfs"] < -40


def test_silence_produces_no_diagnostics():
    _, _, tl = run_offline(np.zeros(FS * 10, np.float32), "speech")
    assert not set().union(*(c for _, c in tl))


def test_broadband_noise_between_phrases():
    talk = ms.render(ms.SpeechLike(FS, amp=0.5, seed=8, pause_prob=0.5), 25, FS)
    noise = ms.render(ms.PinkNoise(0.004, 9), 25, FS)
    eng, a, tl = run_offline(talk + noise, "speech")
    assert a.noise_stats is not None
    assert first_time(tl, "broadband_noise") is not None


def test_confidence_breakdown_is_explicit_and_bounded():
    x = ms.render(ms.HardClip(ms.Percussive(FS, "kick", 0.6, 0.5, seed=3), drive=2.5), 5, FS)
    eng, _, _ = run_offline(x, "kick")
    d = eng.active_for(0)[0]
    b = d.confidence_breakdown
    expected = W_EVIDENCE * b["evidence"] + W_PERSISTENCE * b["persistence"] + W_CONTEXT * b["context"]
    assert d.confidence == pytest.approx(expected, abs=0.002)
    assert 0 <= d.confidence <= 1
    assert {"formula", "evaluable_ticks", "triggered_ticks"} <= set(b)
    assert confidence_label(0.8) == "high" and confidence_label(0.6) == "moderate" and confidence_label(0.3) == "low"


def test_every_diagnostic_has_required_fields():
    x = ms.render(ms.HardClip(ms.Percussive(FS, "kick", 0.6, 0.5, seed=3), drive=2.5), 5, FS)
    eng, _, _ = run_offline(x, "kick")
    d = eng.active_for(0)[0].to_dict()
    for k in ("id", "channel", "category", "severity", "confidence", "timestamp", "evidence", "explanation"):
        assert d[k] not in (None, "", {}), k
    assert "Potential" in d["title"] or d["title"] in ("Clipping",) or "Possible" in d["title"]


def test_diagnostic_clears_when_condition_stops():
    clip = ms.render(ms.HardClip(ms.Percussive(FS, "kick", 0.6, 0.5, seed=3), drive=2.5), 4, FS)
    clean = ms.render(ms.Percussive(FS, "kick", 0.3, 0.5, seed=3), 15, FS)
    _, _, tl = run_offline(np.concatenate([clip, clean]), "kick")
    assert "clipping" in tl[35][1]
    assert "clipping" not in tl[-1][1]


def test_profiles_have_consistent_definitions():
    from app.diagnostics.rules import RULES
    for p in PROFILES.values():
        assert p.rules <= set(RULES), p.key
        assert p.priority <= p.rules | {"feedback_risk"}, p.key
        lo, hi = p.hpf_range
        assert lo < hi
        f = np.array([100.0, 1000.0])
        assert np.all(np.isfinite(p.reference_db(f)))
    assert get_profile("does-not-exist").key == "general"
    # profiles influence thresholds: keyboard is more tolerant than speech
    assert PROFILES["keyboard"].tolerance_db > PROFILES["speech"].tolerance_db
