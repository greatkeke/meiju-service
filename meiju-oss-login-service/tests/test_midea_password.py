"""Offline tests for the real-provider password login (no network).

The request shapes asserted here were recovered from decompiled
`LoginApiService.getLoginId` / `passwordLogin` and verified live where noted in
docs/protocol-notes.md §8.1.
"""

from __future__ import annotations

import pytest

from app.crypto_util import encrypted_password, iam_password
from app.providers.midea import IOT_KEY_PROD, GatewayReply, MideaProvider


def make_provider(iot_key: str = IOT_KEY_PROD) -> MideaProvider:
    return MideaProvider(secret="test-secret", iot_key=iot_key)


def test_get_login_id_sends_bare_body_with_reqid_and_stamp(monkeypatch):
    provider = make_provider()
    captured: dict = {}

    def fake_post(alias: str, body: dict) -> GatewayReply:
        captured["alias"] = alias
        captured["body"] = body
        return GatewayReply(200, 0, "", {"loginId": "lid"})

    monkeypatch.setattr(provider, "_post", fake_post)
    assert provider.get_login_id("13800138000") == "lid"
    assert captured["alias"] == "/v1/user/login/id/get"
    assert captured["body"]["loginAccount"] == "13800138000"
    assert captured["body"]["reqId"]
    assert captured["body"]["stamp"]
    # 裸 body：不能混进 CommonReqData 外壳，否则网关返回 1002
    assert "data" not in captured["body"]
    assert "iotData" not in captured["body"]


def test_get_login_id_propagates_gateway_error(monkeypatch):
    provider = make_provider()
    monkeypatch.setattr(
        provider, "_post", lambda a, b: GatewayReply(200, 1002, "reqId must not be empty", None)
    )
    with pytest.raises(ValueError, match="getLoginId failed"):
        provider.get_login_id("13800138000")


def test_get_login_id_requires_login_id_in_response(monkeypatch):
    provider = make_provider()
    monkeypatch.setattr(provider, "_post", lambda a, b: GatewayReply(200, 0, "", {}))
    with pytest.raises(ValueError, match="no loginId"):
        provider.get_login_id("13800138000")


def test_password_login_builds_recovered_crypto(monkeypatch):
    provider = make_provider()
    captured: dict = {}

    def fake_post(alias: str, body: dict) -> GatewayReply:
        if alias.endswith("/id/get"):
            return GatewayReply(200, 0, "", {"loginId": "lid-42"})
        captured["alias"] = alias
        captured["body"] = body
        return GatewayReply(200, 0, "", {"mdata": {"accessToken": "real-token"}})

    monkeypatch.setattr(provider, "_post", fake_post)
    session = provider.password_login("13800138000", "pw-123")

    assert session.access_token == "real-token"
    assert captured["alias"] == "/v6/mj/user/login"
    body = captured["body"]
    assert set(body) == {"data", "iotData", "timestamp"}
    iot = body["iotData"]
    assert iot["loginAccount"] == "13800138000"
    assert iot["loginId"] == "lid-42"
    assert iot["password"] == encrypted_password("lid-42", "pw-123", IOT_KEY_PROD)
    assert iot["iampwd"] == iam_password("pw-123")
    assert iot["iotAppId"] == "900"
    assert iot["clientType"] == 1


def test_password_login_accepts_explicit_login_id(monkeypatch):
    provider = make_provider()
    captured: dict = {}

    def fake_post(alias: str, body: dict) -> GatewayReply:
        captured["alias"] = alias
        captured["body"] = body
        return GatewayReply(200, 0, "", {"mdata": {"accessToken": "t"}})

    monkeypatch.setattr(provider, "_post", fake_post)
    provider.password_login("13800138000", "pw", login_id="given-id")
    assert captured["alias"] == "/v6/mj/user/login"
    assert captured["body"]["iotData"]["loginId"] == "given-id"


def test_password_login_requires_iot_key():
    provider = make_provider(iot_key="")
    with pytest.raises(NotImplementedError, match="IOT_KEY"):
        provider.password_login("13800138000", "pw")


def test_password_login_surfaces_business_error(monkeypatch):
    provider = make_provider()

    def fake_post(alias: str, body: dict) -> GatewayReply:
        if alias.endswith("/id/get"):
            return GatewayReply(200, 0, "", {"loginId": "lid"})
        return GatewayReply(200, 1100, "password error", None)

    monkeypatch.setattr(provider, "_post", fake_post)
    with pytest.raises(ValueError, match="passwordLogin failed: 1100"):
        provider.password_login("13800138000", "wrong")


def test_iot_key_constants_were_recovered():
    """Guards the values recovered from libKwProtectSDK.so (see harness in meiju-analysis)."""
    assert IOT_KEY_PROD == "PROD_VnoClJI9aikS8dyy"
    assert IOT_KEY_PROD.startswith("PROD_")


def test_reset_and_login_sequence(monkeypatch):
    """短信(type=9) → resetPassword(本地哈希) → getLoginId → passwordLogin。"""
    from app.crypto_util import iam_password, sha256_hex

    provider = make_provider()
    calls: list[str] = []

    def fake_post(alias: str, body: dict, access_token: str = "") -> GatewayReply:
        calls.append(alias)
        if alias == "/mj/user/mobileVerify":
            assert body["iotData"]["type"] == 9
            return GatewayReply(200, 0, "", {"randomCode": "rc-1"})
        if alias == "/mj/user/resetPassword":
            iot = body["iotData"]
            assert iot["randomCode"] == "rc-1"
            assert iot["password"] == sha256_hex("new-pwd")
            assert iot["iampwd"] == iam_password("new-pwd")
            return GatewayReply(200, 0, "", None)
        if alias == "/v1/user/login/id/get":
            return GatewayReply(200, 0, "", {"loginId": "lid-9"})
        if alias == "/v6/mj/user/login":
            assert body["iotData"]["loginId"] == "lid-9"
            return GatewayReply(200, 0, "", {"mdata": {"accessToken": "real-token"}})
        raise AssertionError(f"unexpected alias {alias}")

    monkeypatch.setattr(provider, "_post", fake_post)
    session = provider.reset_and_login("13800138000", "new-pwd", "123456")

    assert session.access_token == "real-token"
    assert calls == [
        "/mj/user/mobileVerify",
        "/mj/user/resetPassword",
        "/v1/user/login/id/get",
        "/v6/mj/user/login",
    ]


def test_reset_and_login_requires_random_code(monkeypatch):
    provider = make_provider()
    monkeypatch.setattr(provider, "_post", lambda a, b, access_token="": GatewayReply(200, 0, "", {}))
    with pytest.raises(ValueError, match="randomCode"):
        provider.reset_and_login("13800138000", "p", "123456")
