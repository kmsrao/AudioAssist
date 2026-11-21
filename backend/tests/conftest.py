from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio.devices import DeviceManager  # noqa: E402
from app.config.settings import AnalysisSettings, ServerConfig  # noqa: E402
from app.database.db import Database  # noqa: E402
from app.database.repository import Repository  # noqa: E402
from app.diagnostics.engine import DiagnosticEngine  # noqa: E402
from app.dsp.analyzer import MultiChannelAnalyzer  # noqa: E402
from app.services.assist_service import AssistService  # noqa: E402
from tests.synth.source import SyntheticSource  # noqa: E402

FS = 48000
NFFT = 4096
RATE = 10.0


class FakeBackend:
    """Stands in for PortAudio in device-discovery tests."""

    def __init__(self) -> None:
        self.devices = [
            {"name": "Built-in Output", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2,
             "default_samplerate": 48000.0, "default_low_input_latency": 0.0, "default_high_input_latency": 0.0},
            {"name": "USB Interface 18x20", "hostapi": 1, "max_input_channels": 18, "max_output_channels": 20,
             "default_samplerate": 48000.0, "default_low_input_latency": 0.005, "default_high_input_latency": 0.02},
            {"name": "Laptop Mic", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 0,
             "default_samplerate": 44100.0, "default_low_input_latency": 0.01, "default_high_input_latency": 0.04},
        ]
        self.hostapis = [{"name": "MME"}, {"name": "ASIO"}]
        self.rescans = 0

    def query_devices(self):
        return self.devices

    def query_hostapis(self):
        return self.hostapis

    def default_input_index(self):
        return 2

    def check_input_settings(self, device, channels, samplerate):
        d = self.devices[device]
        if channels > d["max_input_channels"]:
            raise ValueError("too many channels")
        if d["name"] == "Laptop Mic" and samplerate > 48000:
            raise ValueError("unsupported rate")

    def rescan(self):
        self.rescans += 1


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def repo() -> Repository:
    return Repository(Database(":memory:"))


@pytest.fixture
def server_config(tmp_path) -> ServerConfig:
    return ServerConfig(open_browser=False, data_dir=tmp_path / "data", logs_dir=tmp_path / "logs",
                        frontend_dist=tmp_path / "no-dist")


@pytest.fixture
def service(server_config, repo, fake_backend) -> AssistService:
    def synthetic(s: AnalysisSettings) -> SyntheticSource:
        return SyntheticSource(s.sample_rate, s.block_size, s.channel_count, "demo", realtime=False)

    svc = AssistService(server_config, repo, DeviceManager(fake_backend), run_thread=False,
                        source_factory=synthetic)
    yield svc
    svc.shutdown()


def drive(svc: AssistService, seconds: float) -> None:
    """Advance the synchronous synthetic source and analysis by ``seconds``."""
    blocks_per_tick = int(round(svc.settings.sample_rate / svc.settings.analysis_rate_hz / svc.settings.block_size))
    for _ in range(int(seconds * svc.settings.analysis_rate_hz)):
        svc.source.pump(max(1, blocks_per_tick))
        svc.process_tick()


def run_offline(signal: np.ndarray, profile: str, settings: AnalysisSettings | None = None,
                rate: float = RATE, name: str = "Test"):
    """Feed a mono signal through analyzer + diagnostic engine tick by tick.
    Returns (engine, last analysis, list of (time_s, active categories))."""
    settings = settings or AnalysisSettings()
    mca = MultiChannelAnalyzer(FS, NFFT, rate)
    eng = DiagnosticEngine(settings, rate)
    hop = int(FS / rate)
    buf = np.zeros((NFFT, 1))
    timeline = []
    a = None
    for i in range(len(signal) // hop):
        blk = signal[i * hop:(i + 1) * hop, None].astype(np.float64)
        buf = np.concatenate([buf, blk])[-NFFT:]
        a = mca.process(blk, buf, [0])[0]
        active, _ = eng.evaluate(0, name, profile, a)
        timeline.append(((i + 1) / rate, {d.category for d in active if not d.suppressed_by}))
    return eng, a, timeline


def first_time(timeline, category: str) -> float | None:
    for t, cats in timeline:
        if category in cats:
            return t
    return None
