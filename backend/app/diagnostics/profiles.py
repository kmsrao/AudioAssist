"""Channel profiles.

A profile does NOT impose an EQ preset. It supplies *context* for analysis:

* ``reference`` - a coarse expected long-term spectral shape (dB at
  frequency, interpolated on a log axis). Diagnostics compare the measured
  long-term spectrum against it *after* removing the overall level offset, so
  only shape differences matter. Curves are deliberately smooth and generic
  (speech is based loosely on published long-term average speech spectra);
  they are starting points, not targets to EQ towards.
* ``tolerance_db`` - extra dB of deviation allowed before tonal rules fire.
  Broad/variable sources (keyboard, general) get more slack.
* ``rules`` - which diagnostic rules apply. Bass-heavy sources do not get
  "boominess" or "rumble" rules that would misfire on their legitimate low end.
* ``priority`` - rules that matter most for the source; they get full
  context weight in the confidence calculation, others are down-weighted.
* ``hpf_range`` - typical high-pass corner range when an HPF is suggested.
* ``feedback_sensitivity`` - scales the feedback-risk evidence (sustained
  instruments legitimately produce stable narrow peaks).
* ``target_rms_dbfs`` - nominal average level used for weak-signal advice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

ALL_TONAL = {"muddy", "boxy", "boomy", "low_mid_excess", "harsh", "sibilance", "weak_presence",
             "spectral_concentration"}
ALL_RULES = ALL_TONAL | {"rumble", "hum", "broadband_noise", "hiss", "clipping", "low_headroom",
                         "feedback_risk", "weak_signal"}


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    description: str
    reference: tuple[tuple[float, float], ...]
    rules: frozenset[str]
    priority: frozenset[str]
    tolerance_db: float = 0.0
    hpf_range: tuple[float, float] = (60.0, 100.0)
    feedback_sensitivity: float = 1.0
    target_rms_dbfs: float = -24.0
    rumble_limit_hz: float = 70.0
    notes: str = ""
    analysis_range: tuple[float, float] = (100.0, 8000.0)
    extra: dict = field(default_factory=dict)

    def reference_db(self, freqs: np.ndarray) -> np.ndarray:
        f = np.array([p[0] for p in self.reference])
        v = np.array([p[1] for p in self.reference])
        return np.interp(np.log10(np.maximum(freqs, 1.0)), np.log10(f), v)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "description": self.description,
            "rules": sorted(self.rules), "priority": sorted(self.priority),
            "tolerance_db": self.tolerance_db, "hpf_range": list(self.hpf_range),
            "feedback_sensitivity": self.feedback_sensitivity, "target_rms_dbfs": self.target_rms_dbfs,
            "reference": [list(p) for p in self.reference], "notes": self.notes,
        }


_VOCAL_RULES = frozenset(ALL_RULES - {"low_mid_excess", "spectral_concentration"})
_SPEECH_REF = ((20, -40), (50, -26), (80, -13), (125, -4), (250, 0), (500, 0), (1000, -5), (2000, -9),
               (4000, -13), (8000, -19), (16000, -30), (20000, -36))
_VOCAL_REF = ((20, -42), (50, -28), (80, -15), (125, -6), (250, -1), (400, 1), (600, 1), (1000, -3), (2000, -7),
              (3000, -8), (4000, -10), (8000, -16), (16000, -26), (20000, -32))

PROFILES: dict[str, Profile] = {p.key: p for p in [
    Profile("speech", "Speech", "Spoken word (pastor, presenter, lectern).",
            _SPEECH_REF, _VOCAL_RULES,
            frozenset({"muddy", "boomy", "rumble", "broadband_noise", "hum", "weak_presence", "harsh",
                       "feedback_risk", "clipping", "weak_signal"}),
            hpf_range=(80, 120), feedback_sensitivity=1.2, target_rms_dbfs=-24,
            notes="Prioritises intelligibility: low-frequency control, clarity, noise and harshness."),
    Profile("lead_vocal", "Lead Vocal", "Main sung vocal.",
            _VOCAL_REF, _VOCAL_RULES,
            frozenset({"muddy", "harsh", "sibilance", "weak_presence", "feedback_risk", "clipping", "boxy"}),
            hpf_range=(80, 110), feedback_sensitivity=1.0, target_rms_dbfs=-22,
            notes="Intelligibility, tonal balance, harshness and sibilance."),
    Profile("backing_vocal", "Backing Vocal", "Supporting / harmony vocal.",
            _VOCAL_REF, _VOCAL_RULES,
            frozenset({"muddy", "harsh", "sibilance", "feedback_risk", "clipping", "boxy"}),
            tolerance_db=1.0, hpf_range=(100, 150), feedback_sensitivity=1.0, target_rms_dbfs=-24),
    Profile("choir", "Choir", "Area/choir microphone (distant pickup, high gain).",
            ((20, -42), (50, -30), (80, -18), (125, -8), (250, -1), (500, 0), (1000, -3), (2000, -8),
             (4000, -12), (8000, -19), (16000, -30), (20000, -36)),
            frozenset(ALL_RULES - {"sibilance", "spectral_concentration"}),
            frozenset({"feedback_risk", "muddy", "low_mid_excess", "hum", "broadband_noise", "rumble"}),
            tolerance_db=1.0, hpf_range=(100, 150), feedback_sensitivity=1.3, target_rms_dbfs=-30,
            notes="High-gain distant mics: feedback, low-mid build-up, noise."),
    Profile("acoustic_guitar", "Acoustic Guitar", "Mic'd or DI acoustic guitar.",
            ((20, -40), (50, -24), (80, -10), (125, -2), (250, 0), (500, -2), (1000, -5), (2000, -8),
             (4000, -11), (8000, -16), (16000, -26), (20000, -32)),
            frozenset(ALL_RULES - {"sibilance", "weak_presence"}),
            frozenset({"boomy", "muddy", "boxy", "feedback_risk", "harsh", "clipping"}),
            tolerance_db=1.0, hpf_range=(70, 100), feedback_sensitivity=0.8, target_rms_dbfs=-24),
    Profile("electric_guitar", "Electric Guitar", "Mic'd amp or modeller.",
            ((20, -42), (50, -28), (80, -12), (125, -4), (250, -1), (500, 0), (1000, 0), (2000, -3),
             (4000, -10), (8000, -24), (16000, -40), (20000, -46)),
            frozenset(ALL_RULES - {"sibilance", "weak_presence", "hiss"}),
            frozenset({"muddy", "boxy", "harsh", "hum", "clipping"}),
            tolerance_db=2.0, hpf_range=(70, 100), feedback_sensitivity=0.7, target_rms_dbfs=-22),
    Profile("bass", "Bass", "Bass guitar / synth bass (DI or amp).",
            ((20, -6), (30, -2), (50, 0), (80, 0), (125, -2), (250, -6), (500, -12), (1000, -18), (2000, -24),
             (4000, -32), (8000, -42), (16000, -55), (20000, -60)),
            frozenset({"low_mid_excess", "spectral_concentration", "hum", "broadband_noise", "clipping",
                       "low_headroom", "weak_signal", "boxy"}),
            frozenset({"low_mid_excess", "clipping", "hum", "weak_signal"}),
            tolerance_db=3.0, hpf_range=(25, 40), feedback_sensitivity=0.0, target_rms_dbfs=-20,
            rumble_limit_hz=25.0, analysis_range=(40.0, 2000.0),
            notes="Low-frequency energy is expected; focuses on balance, harmonics and level. Feedback-risk is not "
                  "assessed (typically DI; sustained notes are stable narrow peaks)."),
    Profile("keyboard", "Keyboard", "Piano, keys, synths, organ (wide-range sources).",
            ((20, -24), (30, -14), (60, -6), (125, -2), (250, 0), (500, 0), (1000, -3), (2000, -7),
             (4000, -11), (8000, -17), (16000, -26), (20000, -32)),
            frozenset({"low_mid_excess", "harsh", "rumble", "hum", "broadband_noise", "hiss", "clipping",
                       "low_headroom", "weak_signal"}),
            frozenset({"clipping", "hum", "broadband_noise"}),
            tolerance_db=5.0, hpf_range=(30, 60), feedback_sensitivity=0.0, target_rms_dbfs=-22,
            rumble_limit_hz=30.0, analysis_range=(60.0, 8000.0),
            notes="Broad, conservative logic: no narrow mud/boxy rules; wider tolerances. Feedback-risk is not "
                  "assessed (typically DI; sustained partials are stable narrow peaks)."),
    Profile("kick", "Kick", "Kick drum.",
            ((20, -8), (30, -3), (50, 0), (80, 0), (125, -4), (250, -10), (500, -16), (1000, -18), (2000, -18),
             (4000, -20), (8000, -28), (16000, -40), (20000, -46)),
            frozenset({"boxy", "clipping", "low_headroom", "weak_signal"}),
            frozenset({"boxy", "clipping"}),
            tolerance_db=3.0, hpf_range=(25, 40), feedback_sensitivity=0.0, target_rms_dbfs=-20,
            rumble_limit_hz=25.0, analysis_range=(40.0, 6000.0),
            notes="Low-frequency weight and attack expected; checks boxiness and overload. Hum is not assessed "
                  "(the drum's own ~50-60 Hz body makes a mains check unreliable)."),
    Profile("snare", "Snare", "Snare drum.",
            ((20, -36), (50, -24), (80, -14), (125, -6), (200, 0), (500, -2), (1000, -4), (2000, -5),
             (4000, -6), (8000, -10), (16000, -20), (20000, -26)),
            frozenset({"boxy", "rumble", "harsh", "clipping", "low_headroom", "weak_signal", "hum"}),
            frozenset({"boxy", "rumble", "clipping"}),
            tolerance_db=3.0, hpf_range=(80, 120), feedback_sensitivity=0.0, target_rms_dbfs=-20),
    Profile("cymbals", "Cymbals / Overheads", "Overheads, hi-hat, cymbal mics.",
            ((20, -50), (100, -36), (200, -26), (500, -16), (1000, -10), (2000, -5), (4000, -2), (8000, 0),
             (16000, -6), (20000, -10)),
            frozenset({"rumble", "harsh", "clipping", "low_headroom", "weak_signal", "low_mid_excess"}),
            frozenset({"rumble", "harsh"}),
            tolerance_db=3.0, hpf_range=(150, 300), feedback_sensitivity=0.0, target_rms_dbfs=-26,
            rumble_limit_hz=120.0, analysis_range=(300.0, 16000.0)),
    Profile("general", "General", "Unknown or mixed source. Most conservative.",
            ((20, -24), (30, -12), (60, -4), (125, 0), (250, 0), (500, 0), (1000, -2), (2000, -4),
             (4000, -7), (8000, -11), (16000, -18), (20000, -24)),
            frozenset(ALL_RULES - {"sibilance", "weak_presence", "muddy", "boxy"}),
            frozenset({"clipping", "feedback_risk", "hum"}),
            tolerance_db=4.0, hpf_range=(40, 80), feedback_sensitivity=0.6, target_rms_dbfs=-24,
            rumble_limit_hz=35.0),
]}


def get_profile(key: str | None) -> Profile:
    return PROFILES.get(key or "general", PROFILES["general"])
