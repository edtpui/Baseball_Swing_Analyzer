"""End-to-end through the web server with the simulated sensor."""

import pytest
from fastapi.testclient import TestClient

from server.app import ServerOptions, create_app


@pytest.fixture()
def client(tmp_path):
    options = ServerOptions(data_dir=tmp_path, web_dir=tmp_path / "no-dashboard", simulate=True, sim_seed=4)
    with TestClient(create_app(options)) as test_client:
        yield test_client


def _wait_for(ws, kind, limit=400):
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind!r} message within {limit} messages")


def test_swing_flows_from_sensor_to_browser_and_disk(client):
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["simulate"] is True
        live = _wait_for(ws, "live")["live"]
        assert len(live["q"]) == 4 and live["state"] == "idle"

        assert client.post("/api/sim/swing").status_code == 202
        summary = _wait_for(ws, "summary")["summary"]
        swing = _wait_for(ws, "swing")["swing"]

    assert swing["device_swing_id"] == summary["swing_id"]
    assert 15.0 < swing["metrics"]["bat_speed_mps"] < 45.0
    assert swing["quality"]["confidence"] in ("high", "reduced")

    listed = client.get("/api/swings").json()
    assert [s["id"] for s in listed] == [swing["id"]]
    assert client.get(f"/api/swings/{swing['id']}").json()["path"] == swing["path"]

    assert client.delete(f"/api/swings/{swing['id']}").status_code == 204
    assert client.get("/api/swings").json() == []
    assert client.get(f"/api/swings/{swing['id']}").status_code == 404


def test_changing_bat_length_reanalyses_stored_swings(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/sim/swing")
        before = _wait_for(ws, "swing")["swing"]

    settings = client.get("/api/state").json()["settings"]
    settings["bat_length_m"] = 0.762  # 30 in
    assert client.put("/api/settings", json=settings).status_code == 200

    after = client.get(f"/api/swings/{before['id']}").json()
    assert after["bat"]["length_m"] == pytest.approx(0.762)
    assert after["metrics"]["bat_speed_mps"] < before["metrics"]["bat_speed_mps"]


def test_settings_are_validated(client):
    settings = client.get("/api/state").json()["settings"]
    assert client.put("/api/settings", json={**settings, "bat_length_m": 3.0}).status_code == 422
    assert client.put("/api/settings", json={**settings, "start_dps": 800, "min_peak_dps": 850}).status_code == 422
    assert client.get("/api/state").json()["settings"] == settings


def test_swing_ids_cannot_escape_the_data_folder(client):
    assert client.get("/api/swings/..%2Fsettings").status_code == 404
    assert client.delete("/api/swings/not-an-id").status_code == 404
