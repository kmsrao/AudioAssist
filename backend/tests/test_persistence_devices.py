"""SQLite persistence and device discovery abstractions."""
import pytest

from app.audio.devices import DeviceManager
from app.config.settings import AnalysisSettings
from app.database.db import Database, SchemaVersion, SCHEMA_VERSION
from app.database.repository import Repository
from app.models.schemas import ChannelConfig, default_channels


def test_settings_roundtrip_and_defaults(tmp_path):
    repo = Repository(Database(tmp_path / "t.db"))
    assert repo.load_settings() == AnalysisSettings()
    s = AnalysisSettings(fft_size=8192, min_confidence=0.7)
    repo.save_settings(s)
    repo2 = Repository(Database(tmp_path / "t.db"))  # reopen file
    assert repo2.load_settings().fft_size == 8192
    assert repo2.load_settings().min_confidence == 0.7


def test_schema_version_recorded(tmp_path):
    db = Database(tmp_path / "v.db")
    with db.session() as s:
        assert s.get(SchemaVersion, 1).version == SCHEMA_VERSION


def test_corrupt_settings_fall_back_to_defaults(repo):
    repo._put("analysis_settings", {"fft_size": 123})
    assert repo.load_settings() == AnalysisSettings()


def test_settings_validation():
    with pytest.raises(ValueError):
        AnalysisSettings(sample_rate=12345)
    with pytest.raises(ValueError):
        AnalysisSettings(hpf_min_hz=300, hpf_max_hz=200)


def test_channels_roundtrip(repo):
    chans = default_channels(4)
    chans[1] = ChannelConfig(index=1, name="Lead", profile="lead_vocal", group="Vocals", selected=False)
    repo.save_channels(chans)
    out = repo.load_channels()
    assert [c.name for c in out] == ["Pastor Mic", "Lead", "Choir Mic", "Keyboard"]
    assert out[1].selected is False and out[1].profile == "lead_vocal"


def test_channel_profile_validation():
    with pytest.raises(ValueError):
        ChannelConfig(index=0, name="x", profile="tuba")


def test_recommendation_and_diagnostic_history(repo):
    rec = {"id": "r1", "diagnostic_id": "d1", "channel": 3, "channel_name": "Choir", "category": "hum",
           "status": "new", "created_at": 1.0, "updated_at": 1.0, "summary": "x"}
    repo.upsert_recommendation(rec)
    repo.upsert_recommendation(rec | {"status": "dismissed", "updated_at": 2.0})
    hist = repo.recommendation_history()
    assert len(hist) == 1 and hist[0]["status"] == "dismissed"
    assert repo.recommendation_history(status="new") == []
    d = {"id": "d1", "channel": 3, "channel_name": "Choir", "profile": "choir", "category": "hum",
         "severity": "medium", "confidence": 0.8, "first_seen": 1.0, "last_seen": 2.0, "status": "active",
         "explanation": "e", "evidence": {"a": 1}}
    repo.upsert_diagnostic(d)
    assert repo.diagnostic_history(channel=3)[0]["evidence"] == {"a": 1}


def test_operator_actions_logged(repo):
    repo.upsert_recommendation({"id": "r9", "diagnostic_id": "d", "channel": 1, "channel_name": "a",
                                "category": "muddy", "status": "new", "created_at": 1.0, "updated_at": 1.0})
    repo.log_action("recommendation_applied", "CH01", "r9", {"note": "cut 3 dB"})
    a = repo.actions()[0]
    assert a["action"] == "recommendation_applied" and a["payload"]["note"] == "cut 3 dB"


def test_groups_and_device_history(repo):
    assert "Vocals" in repo.load_groups()
    repo.save_groups(["Band", "Speech"])
    assert repo.load_groups() == ["Band", "Speech"]
    repo.remember_device("USB", "ASIO", 1, 48000, 512, 18)
    repo.remember_device("USB", "ASIO", 2, 48000, 256, 18)
    h = repo.device_history()
    assert len(h) == 1 and h[0]["device_index"] == 2 and h[0]["block_size"] == 256


# ------------------------------------------------------------------ devices
def test_device_discovery_lists_inputs_only(fake_backend):
    dm = DeviceManager(fake_backend)
    devs = dm.list_input_devices()
    assert [d.name for d in devs] == ["USB Interface 18x20", "Laptop Mic"]
    usb = devs[0]
    assert usb.hostapi == "ASIO" and usb.max_input_channels == 18
    assert usb.supported_sample_rates == [44100, 48000, 88200, 96000]
    assert devs[1].supported_sample_rates == [44100, 48000] and devs[1].is_default_input


def test_device_lookup_and_validation(fake_backend):
    dm = DeviceManager(fake_backend)
    assert dm.get_device(0) is None  # output-only
    assert dm.get_device(99) is None
    assert dm.find_by_name("Laptop Mic").index == 2
    assert dm.find_by_name("Laptop Mic", hostapi="ASIO") is None
    ok, _ = dm.validate_config(1, 16, 48000)
    assert ok
    ok, msg = dm.validate_config(2, 8, 48000)
    assert not ok and "at most 2" in msg


def test_device_rescan(fake_backend):
    dm = DeviceManager(fake_backend)
    dm.list_input_devices(refresh=True)
    assert fake_backend.rescans == 1
