"""REST API, WebSocket and service integration (synthetic audio, fake device backend)."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import drive


@pytest.fixture
def client(server_config, service):
    app = create_app(server_config, service=service)
    with TestClient(app) as c:  # lifespan starts synthetic capture (synchronous mode)
        yield c


def test_status_and_health(client, service):
    drive(service, 1.0)
    st = client.get("/api/status").json()
    assert "mock" not in st and st["auto_modifies_audio"] is False
    assert st["capture"]["state"] == "running" and st["sample_rate"] == 48000
    assert st["channels_analyzed"] == 8
    h = client.get("/api/health").json()
    assert h["processing"]["ticks"] >= 10 and "cpu_percent" in h


def test_devices_endpoints(client):
    d = client.get("/api/devices").json()
    assert d["available"] and [x["name"] for x in d["devices"]] == ["USB Interface 18x20", "Laptop Mic"]
    assert 48000 in d["options"]["sample_rates"]
    assert client.get("/api/devices/1").json()["max_input_channels"] == 18
    assert client.get("/api/devices/0").status_code == 404


def test_channels_get_put_and_selection(client, service):
    chans = client.get("/api/channels").json()
    assert len(chans["channels"]) == 8 and chans["channels"][0]["number"] == 1
    edit = chans["channels"][0] | {"name": "Lectern", "profile": "speech", "group": "Speech"}
    r = client.put("/api/channels", json={"channels": [edit]})
    assert r.status_code == 200 and r.json()["channels"][0]["name"] == "Lectern"
    bad = client.put("/api/channels", json={"channels": [edit | {"profile": "kazoo"}]})
    assert bad.status_code == 422
    assert client.put("/api/channels/selected", json={"deselect_all": True}).json()["selected"] == []
    assert client.put("/api/channels/selected", json={"group": "Drums"}).json()["selected"] == [7, 8]
    assert client.put("/api/channels/selected", json={"add_group": "Speech"}).json()["selected"] == [1, 7, 8]
    assert client.put("/api/channels/selected", json={"selected": [2, 3]}).json()["selected"] == [2, 3]
    assert client.get("/api/channels/selected").json()["selected"] == [2, 3]
    assert client.put("/api/channels/selected", json={"group": "Nope"}).status_code == 400
    assert client.put("/api/channels/selected", json={"select_all": True}).json()["selected"] == list(range(1, 9))


def test_only_selected_channels_are_analyzed(client, service):
    client.put("/api/channels/selected", json={"selected": [2]})
    drive(service, 12)
    diags = client.get("/api/diagnostics").json()["active"]
    recs = client.get("/api/recommendations").json()["active"]
    assert diags and all(d["channel"] == 2 for d in diags)
    assert all(r["channel"] == 2 for r in recs)
    assert client.get("/api/channels/1/analysis").status_code == 404
    a = client.get("/api/channels/2/analysis?spectrogram=true").json()
    assert len(a["spectrum"]["db"]) == len(client.get("/api/analysis/grid").json()["freqs"])
    assert a["spectrogram"] and len(a["spectrogram"][0]) == 128


def test_recommendation_flow_apply_dismiss_history(client, service):
    drive(service, 12)
    recs = client.get("/api/recommendations").json()["active"]
    cats = {r["category"] for r in recs}
    assert {"clipping", "rumble", "feedback_risk"} <= cats
    clip = next(r for r in recs if r["category"] == "clipping")
    for k in ("channel", "channel_name", "profile", "issue", "severity", "confidence", "control",
              "explanation", "expected_improvement", "supporting_analysis", "timestamp", "status"):
        assert k in clip
    r = client.post(f"/api/recommendations/{clip['id']}/applied", json={"note": "trim -6"}).json()
    assert r["status"] == "applied" and r["before"] is not None
    again = client.post(f"/api/recommendations/{clip['id']}/applied").json()  # further adjustment is allowed
    assert again["status"] == "applied" and again["verification"]["state"] == "pending"
    re = client.post(f"/api/recommendations/{clip['id']}/reanalyze").json()
    assert re["verification"]["state"] == "pending"
    other = next(r for r in recs if r["category"] == "rumble")
    assert client.post(f"/api/recommendations/{other['id']}/dismissed", json={"note": "intentional"}).json()["status"] == "dismissed"
    assert client.get(f"/api/recommendations/{other['id']}").json()["status"] == "dismissed"
    assert client.post(f"/api/recommendations/{other['id']}/applied").status_code == 409
    hist = client.get("/api/recommendations/history").json()["history"]
    assert {h["id"] for h in hist} >= {clip["id"], other["id"]}
    acts = client.get("/api/operator-actions").json()["actions"]
    assert {"recommendation_applied", "recommendation_dismissed", "reanalyze"} <= {a["action"] for a in acts}
    assert client.post("/api/recommendations/missing/dismissed").status_code == 404
    assert client.get("/api/diagnostics/history").json()["history"]


def test_reanalyze_endpoints(client, service):
    drive(service, 2)
    assert client.post("/api/channels/3/reanalyze").json()["channel"] == 3
    assert client.post("/api/channels/99/reanalyze").status_code == 404
    assert len(client.post("/api/reanalyze").json()["reanalyzed"]) == 8


def test_capture_stop_start_and_change_source(client, service):
    assert client.post("/api/capture/stop").json()["state"] == "stopped"
    assert client.post("/api/capture/start").json()["state"] == "running"
    r = client.post("/api/capture/device", json={"device_index": 1, "channel_count": 4})
    assert r.status_code == 200 and r.json()["source"]["channels"] == 4
    assert service.settings.device_name == "USB Interface 18x20"
    assert len(client.get("/api/channels").json()["channels"]) == 4
    assert client.post("/api/capture/device", json={"device_index": 0}).status_code == 400
    assert client.post("/api/capture/device", json={}).status_code == 422


def test_settings_update_validation_and_reset(client, service):
    s = client.get("/api/settings").json()
    assert s["settings"]["fft_size"] == 4096 and 8192 in s["options"]["fft_sizes"]
    r = client.post("/api/settings", json={"values": {"fft_size": 8192, "min_confidence": 0.7}})
    assert r.status_code == 200 and service.analyzer.nfft == 8192
    assert client.post("/api/settings", json={"values": {"fft_size": 1000}}).status_code == 422
    assert client.post("/api/settings", json={"values": {"nonsense": 1}}).status_code == 422
    reset = client.post("/api/settings/reset").json()["settings"]
    assert reset["fft_size"] == 4096 and reset["min_confidence"] == 0.55 and "source" not in reset


def test_groups_and_profiles(client):
    assert len(client.get("/api/profiles").json()["profiles"]) == 12
    assert client.put("/api/groups", json={"groups": ["Band", "Speech", "Band"]}).json()["groups"] == ["Band", "Speech"]
    assert client.put("/api/groups", json={"groups": [" "]}).status_code == 400


def test_spa_fallback_when_frontend_missing(client):
    r = client.get("/")
    assert r.status_code == 200 and "backend is running" in r.text
    assert client.get("/api/does-not-exist").status_code == 404


def test_websocket_stream(client, service):
    drive(service, 3)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and len(hello["channels"]) == 8 and hello["grid"]["freqs"]
        ws.send_json({"type": "focus", "channel": 5})
        seen = {}
        for _ in range(60):
            m = ws.receive_json()
            seen.setdefault(m["type"], m)
            if m["type"] == "analysis" and m["channel"] == 5 and m.get("full"):
                seen["analysis"] = m
                break
        assert "levels" in seen and len(seen["levels"]["levels"]) == 8
        assert "status" in seen and "recommendations" in seen
        an = seen["analysis"]
        assert an["channel"] == 5 and "spectrogram" in an and an["bands"]
