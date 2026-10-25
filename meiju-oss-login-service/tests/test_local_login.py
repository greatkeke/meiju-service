from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.store import store

client = TestClient(app)


def test_local_sms_login_and_me():
    store._sms.clear()
    store._sessions.clear()
    store._by_refresh.clear()

    response = client.post("/v1/login/request-sms", json={"mobile": "13800138000"})
    assert response.status_code == 200

    debug = client.get("/v1/login/debug/local-code", params={"mobile": "13800138000"})
    assert debug.status_code == 200
    code = debug.json()["code"]

    login = client.post(
        "/v1/login/verify-sms",
        json={"mobile": "13800138000", "sms_code": code},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["access_token"].startswith("local_access_")
    assert tokens["refresh_token"].startswith("local_refresh_")

    me = client.get("/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["mobile"] == "13800138000"

    refreshed = client.post("/v1/login/refresh-token", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != tokens["access_token"]


def test_reject_invalid_sms_code():
    store._sms.clear()
    client.post("/v1/login/request-sms", json={"mobile": "13900139000"})
    response = client.post(
        "/v1/login/verify-sms",
        json={"mobile": "13900139000", "sms_code": "000000"},
    )
    assert response.status_code == 400
