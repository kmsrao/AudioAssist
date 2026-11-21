"""Offline calibration harness: runs a synthetic test scenario through the analyzer and
diagnostic engine faster than real time and prints what would be raised.

usage: python scripts/calibrate.py [scenario] [seconds] [profiles comma-separated]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np  # noqa: E402

from tests.synth.scenarios import build_channel_generators  # noqa: E402
from app.config.settings import AnalysisSettings  # noqa: E402
from app.diagnostics.engine import DiagnosticEngine  # noqa: E402
from app.diagnostics.rules import deviation_curve  # noqa: E402
from app.diagnostics.profiles import get_profile  # noqa: E402
from app.dsp.analyzer import MultiChannelAnalyzer  # noqa: E402

DEMO_PROFILES = ["speech", "lead_vocal", "choir", "keyboard", "acoustic_guitar", "bass", "kick", "snare"]


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "demo"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30
    profiles = sys.argv[3].split(",") if len(sys.argv) > 3 else DEMO_PROFILES
    n = len(profiles)
    fs, nfft, rate = 48000, 4096, 10
    s = AnalysisSettings()
    gens = build_channel_generators(scenario, fs, n)
    mca = MultiChannelAnalyzer(fs, nfft, rate)
    eng = DiagnosticEngine(s, rate)
    hop = fs // rate
    buf = np.zeros((nfft, n))
    raised: dict[int, dict[str, float]] = {i: {} for i in range(n)}
    for tick in range(int(seconds * rate)):
        blk = np.stack([g.render(hop) for g in gens], axis=1)
        buf = np.concatenate([buf, blk])[-nfft:]
        res = mca.process(blk, buf, list(range(n)))
        for ch, a in res.items():
            active, _ = eng.evaluate(ch, f"CH{ch + 1}", profiles[ch], a)
            for d in active:
                raised[ch].setdefault(d.category, tick / rate)
    for ch in range(n):
        a = mca.channels[ch].last
        p = get_profile(profiles[ch])
        f, d, _ = deviation_curve(a, p)
        pts = [125, 250, 400, 700, 1500, 3500, 7000]
        devs = " ".join(f"{x}:{np.interp(x, f, d):+.1f}" for x in pts)
        act = eng.active_for(ch)
        print(f"CH{ch + 1} {profiles[ch]:15s} sig={a.signal_dbfs} sib90={a.sibilance_p90_db and round(a.sibilance_p90_db, 1)}")
        print(f"    dev {devs}")
        for dg in act:
            print(f"    -> {dg.category:22s} sev={dg.severity:8s} conf={dg.confidence:.2f} target={dg.target_hz} "
                  f"first@{raised[ch].get(dg.category)}s supp={dg.suppressed_by}")


if __name__ == "__main__":
    main()
