from __future__ import annotations

import json
import urllib.request

from app.main import app


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class FakeClient:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.params: dict[str, object] = {}

    def post(self, path: str, json: dict[str, object]) -> FakeResponse:
        return _call(path, "POST", json, self.headers, {})

    def get(self, path: str, headers: dict[str, str] | None = None, params: dict[str, object] | None = None) -> FakeResponse:
        self.headers = headers or {}
        self.params = params or {}
        return _call(path, "GET", None, self.headers, self.params)


def _call(path: str, method: str, body: object, headers: dict[str, str], params: dict[str, object]) -> FakeResponse:
    from app.store import store
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.request(method, path, json=body, headers=headers, params=params)
    return FakeResponse(response.status_code, response.json())


def main() -> None:
    from app.store import store

    store._sms.clear()
    store._sessions.clear()
    store._by_refresh.clear()
    client = FakeClient()

    sms = client.post("/v1/login/request-sms", json={"mobile": "13800138000"})
    assert sms.status_code == 200, sms.json()

    code = client.get("/v1/login/debug/local-code", params={"mobile": "13800138000"}).json()["code"]
    login = client.post(
        "/v1/login/verify-sms",
        json={"mobile": "13800138000", "sms_code": code},
    )
    assert login.status_code == 200, login.json()
    tokens = login.json()

    me = client.get("/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200, me.json()

    refreshed = client.post("/v1/login/refresh-token", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.json()

    print(json.dumps({"sms": sms.json(), "login": login.json(), "me": me.json(), "refresh": refreshed.json()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
