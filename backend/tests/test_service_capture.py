"""Capture lifecycle: disconnect detection, recovery, device switching, channel selection."""
import numpy as np

from app.audio import sources
from app.audio.sources import AudioSource
from app.services import assist_service

from .conftest import drive


class FakeDeviceSource(AudioSource):
    """Stands in for SoundDeviceSource (no PortAudio) - records construction args."""
    kind = "device"
    created: list = []

    def __init__(self, device_index, sample_rate, block_size, channels, device_name="", hostapi=""):
        super().__init__(sample_rate, block_size, channels)
        self.device_index, self.device_name, self.hostapi = device_index, device_name, hostapi
        self._running = False
        FakeDeviceSource.created.append(self)

    @property
    def running(self):
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def describe(self):
        return super().describe() | {"device_name": self.device_name, "hostapi": self.hostapi}


def test_watchdog_detects_disconnect_and_recovers(service):
    service.start_capture()
    assert service.capture_state == "running"
    service.source.finished.set()  # what PortAudio's finished_callback does on device loss
    service._watchdog()
    assert service.capture_state == "error" and service.source is None
    assert "disconnected" in service.capture_error
    service._last_recovery = 0.0  # pretend the retry interval elapsed
    service._watchdog()
    assert service.capture_state == "running" and service.source is not None


def test_watchdog_flags_stalled_callbacks(service):
    service.start_capture()
    service.source.pump(2)
    service.source.stats.last_callback_mono -= 5.0
    service._watchdog()
    assert service.capture_state == "stalled"
    service.source.pump(1)
    service._watchdog()
    assert service.capture_state == "running"


def test_change_to_hardware_device(service, monkeypatch):
    monkeypatch.setattr(assist_service, "SoundDeviceSource", FakeDeviceSource)
    service._source_factory = None
    out = service.change_device(device_index=1, sample_rate=48000, block_size=256, channel_count=16)
    assert out["state"] == "running"
    src = FakeDeviceSource.created[-1]
    assert src.device_name == "USB Interface 18x20" and src.hostapi == "ASIO" and src.channels == 16
    assert service.settings.device_name == "USB Interface 18x20"
    assert len(service.visible_channels()) == 16


def test_no_configured_device_uses_system_default_input(service, monkeypatch):
    monkeypatch.setattr(assist_service, "SoundDeviceSource", FakeDeviceSource)
    service._source_factory = None
    assert service.settings.device_index is None and service.settings.device_name is None
    service.start_capture()
    src = FakeDeviceSource.created[-1]
    assert service.capture_state == "running" and src.device_name == "Laptop Mic" and src.channels == 2


def test_device_change_rejects_unsupported_rate(service):
    import pytest
    with pytest.raises(ValueError):
        service.change_device(device_index=2, sample_rate=96000, block_size=None, channel_count=None)


def test_missing_saved_device_reports_error(service, monkeypatch):
    monkeypatch.setattr(assist_service, "SoundDeviceSource", FakeDeviceSource)
    service._source_factory = None
    service.settings = service.settings.model_copy(update={"device_name": "Unplugged Interface",
                                                           "device_index": None})
    import pytest
    with pytest.raises(RuntimeError):
        service.start_capture()
    assert service.capture_state == "error" and "not found" in service.capture_error


def test_unselected_channels_get_no_diagnostics(service):
    service.start_capture()
    service.update_selection(selected=[7])
    drive(service, 6)
    assert {d.channel for d in service.engine.active_for()} <= {7}
    assert set(service.analyzer.channels) == {6}
    # all channels still report live activity for the channel strip
    assert len(service.levels) == 8 and service.levels[0]["rms"] is not None


def test_ring_buffer_overrun_is_counted(service):
    service.start_capture()
    src = service.source
    src.pump(int(sources.RING_SECONDS * 48000 / 512) + 50)  # more than the ring holds
    service.process_tick()
    assert service.frames_lost > 0


def test_reset_channel_clears_evidence(service):
    service.start_capture()
    drive(service, 8)
    assert service.engine.active_for(6)  # kick clipping
    service.reset_channel(6)
    an = service.analyzer.channels[6]
    assert an.active_frames == 0 and np.all(an.long_power == 0)


def test_applied_but_unfixed_issue_is_not_falsely_resolved(service):
    """Regression: resetting evidence on "applied" must not make a persisting problem look resolved."""
    service.start_capture()
    drive(service, 6)
    rec = next(r for r in service.recs.list_active() if r.category == "clipping")
    service.apply_recommendation(rec.id, "trim -6 (but source still clips)")
    drive(service, 12)  # synthetic kick keeps clipping
    assert rec.status == "applied"
    assert rec.verification["state"] in ("persists", "improved")


def test_applied_and_fixed_issue_resolves(service):
    service.start_capture()
    drive(service, 6)
    rec = next(r for r in service.recs.list_active() if r.category == "clipping")
    service.apply_recommendation(rec.id)
    # simulate the operator's fix: replace the kick generator with a clean, quieter one
    from tests.synth import signals as ms
    service.source._gens[6] = ms.Percussive(48000, "kick", amp=0.3, interval_s=0.5, seed=1)
    service.source._loop = None
    drive(service, 12)
    assert rec.status == "resolved" and rec.verification["state"] == "resolved"
