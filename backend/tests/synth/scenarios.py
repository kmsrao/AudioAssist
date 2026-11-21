"""Per-channel synthetic test scenarios.

The ``demo`` scenario plants one realistic problem per channel so every part
of the pipeline can be exercised without hardware:

=====  ================  ===================================================
CH     Content           Planted issue
=====  ================  ===================================================
1      speech            +12 dB low-mid bump at 300 Hz -> muddiness
2      sung vocal        +12 dB at 4 kHz               -> harshness
3      choir (3 voices)  60 Hz hum + harmonics         -> hum
4      keyboard chords   none (false-positive check)
5      acoustic guitar   steady ringing 2.8 kHz tone    -> feedback risk
6      bass              very low level               -> weak signal
7      kick              overdriven preamp            -> clipping
8      snare             strong sub-40 Hz rumble       -> rumble / HPF
=====  ================  ===================================================

Channels beyond 8 cycle with different seeds.
"""
from __future__ import annotations

from collections.abc import Callable

from tests.synth import signals as ms

Factory = Callable[[float, int], ms.Generator]


def _speech_muddy(fs: float, seed: int) -> ms.Generator:
    return ms.colored(ms.SpeechLike(fs, amp=0.4, f0=115, seed=seed), fs, 300, 12.0, 1.0)


def _vocal_harsh(fs: float, seed: int) -> ms.Generator:
    return ms.colored(ms.SpeechLike(fs, amp=0.5, f0=300, seed=seed, sustained=True), fs, 4000, 12.0, 1.2)


def _choir_hum(fs: float, seed: int) -> ms.Generator:
    voices = [ms.SpeechLike(fs, amp=0.15, f0=f, seed=seed + i, sustained=True, pause_prob=0.05)
              for i, f in enumerate((200, 250, 300))]
    return ms.Sum(*voices, ms.Hum(fs, 60.0, amp=0.006))


def _keyboard(fs: float, seed: int) -> ms.Generator:
    notes = [196.0, 261.6, 329.6, 392.0, 523.3, 659.3, 784.0]
    return ms.Plucked(fs, notes, amp=0.07, interval_s=0.8, decay_s=1.5, harmonics=20, rolloff=0.7, seed=seed, chord=3)


def _guitar_feedback(fs: float, seed: int) -> ms.Generator:
    notes = [146.8, 196.0, 246.9, 329.6, 392.0, 493.9, 587.3]
    guitar = ms.colored(ms.Plucked(fs, notes, amp=0.08, interval_s=0.5, decay_s=0.9, harmonics=20, rolloff=0.7,
                                   seed=seed, chord=2), fs, 330, -6.0, 0.8)
    # steady ring (a rising ramp would restart at every loop of the cached synthetic audio)
    ring = ms.Sine(fs, 2810.0, amp=0.025)
    return ms.Sum(guitar, ring, ms.PinkNoise(0.0005, seed))


def _bass_weak(fs: float, seed: int) -> ms.Generator:
    notes = [41.2, 55.0, 61.7, 73.4, 82.4]
    return ms.Plucked(fs, notes, amp=0.006, interval_s=0.5, decay_s=0.8, harmonics=5, rolloff=1.5, seed=seed)


def _kick_clipping(fs: float, seed: int) -> ms.Generator:
    return ms.HardClip(ms.Percussive(fs, "kick", amp=0.6, interval_s=0.5, seed=seed), drive=2.5)


def _snare_rumble(fs: float, seed: int) -> ms.Generator:
    return ms.Sum(ms.Percussive(fs, "snare", amp=0.35, interval_s=1.0, seed=seed),
                  ms.Rumble(fs, amp=0.12, cutoff=40.0, seed=seed))


def _speech_clean(fs: float, seed: int) -> ms.Generator:
    return ms.Sum(ms.SpeechLike(fs, amp=0.8, f0=120, seed=seed), ms.PinkNoise(0.0002, seed))


def _noise_floor(fs: float, seed: int) -> ms.Generator:
    return ms.PinkNoise(0.0003, seed)


DEMO: list[Factory] = [
    _speech_muddy, _vocal_harsh, _choir_hum, _keyboard,
    _guitar_feedback, _bass_weak, _kick_clipping, _snare_rumble,
]

SCENARIOS: dict[str, list[Factory]] = {
    "demo": DEMO,
    "clean": [_speech_clean, _keyboard, _noise_floor],
    "silence": [lambda fs, seed: ms.Silence()],
}


def build_channel_generators(scenario: str, fs: float, channels: int) -> list[ms.Generator]:
    factories = SCENARIOS.get(scenario, DEMO)
    gens = [factories[i % len(factories)](fs, 1000 + i) for i in range(channels)]
    if scenario == "silence":
        return gens
    # Real inputs never deliver digital silence: add a -80 dBFS pink self-noise floor.
    return [ms.Sum(g, ms.PinkNoise(0.0001, 5000 + i)) for i, g in enumerate(gens)]
