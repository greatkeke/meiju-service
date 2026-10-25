from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.store import store

client = TestClient(app)


def _login() -> dict[str, str]:
    store._sms.clear()
    store._sessions.clear()
    store._by_refresh.clear()

    assert client.post("/v1/login/request-sms", json={"mobile": "13800138000"}).status_code == 200
    code = client.get("/v1/login/debug/local-code", params={"mobile": "13800138000"}).json()["code"]
    tokens = client.post(
        "/v1/login/verify-sms", json={"mobile": "13800138000", "sms_code": code}
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_device_list_requires_auth():
    assert client.get("/v1/user/device/list").status_code == 401


def test_device_list_shape():
    headers = _login()
    body = client.get("/v1/user/device/list", headers=headers).json()
    assert body["code"] == 0
    devices = body["data"]["applianceList"]
    assert len(devices) >= 3
    ac = next(d for d in devices if d["type"] == "0xAC")
    assert ac["onlineStatus"] == 1
    assert set(ac["props"]) >= {"power", "mode", "targetTemperature", "fanSpeed", "swing"}


def test_set_properties_roundtrip():
    headers = _login()
    response = client.post(
        "/v1/thing/batch/properties",
        headers=headers,
        json={
            "applianceCode": 1234500001,
            "props": [
                {"code": "power", "value": "on"},
                {"code": "targetTemperature", "value": 24},
                {"code": "mode", "value": "cool"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    props = response.json()["data"]["props"]
    assert props["power"] == "on"
    assert props["targetTemperature"] == 24

    state = client.get("/v1/thing/properties/1234500001", headers=headers).json()
    assert state["data"]["props"]["targetTemperature"] == 24


def test_rejects_illegal_temperature_and_unknown_prop():
    headers = _login()
    too_hot = client.post(
        "/v1/thing/batch/properties",
        headers=headers,
        json={"applianceCode": 1234500001, "props": [{"code": "targetTemperature", "value": 45}]},
    )
    assert too_hot.status_code == 400

    unknown = client.post(
        "/v1/thing/batch/properties",
        headers=headers,
        json={"applianceCode": 1234500001, "props": [{"code": "volume", "value": 3}]},
    )
    assert unknown.status_code == 400


def test_tsl_action_sets_power():
    headers = _login()
    response = client.post(
        "/v1/thing/action/1234500003",
        headers=headers,
        json={"method": "setProperties", "params": {"power": "on", "brightness": 42}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["props"]["power"] == "on"
    assert body["data"]["props"]["brightness"] == 42
