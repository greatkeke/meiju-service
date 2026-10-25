"""Offline tests for the real-device forwarding layer."""

from __future__ import annotations

import pytest

from app import real_devices
from app.providers.midea import GatewayReply, MideaProvider
from app.real_devices import FALLBACK_SCHEMA, RealDevices


def make(monkeypatch, responder):
    provider = MideaProvider(secret="s", iot_key="k")
    calls: list[tuple[str, dict]] = []

    def fake_post(alias, body, access_token=""):
        calls.append((alias, body))
        return responder(alias, body)

    monkeypatch.setattr(provider, "_post", fake_post)
    return RealDevices(provider, "tok-123"), calls


def test_devices_maps_list_and_template(monkeypatch):
    def responder(alias, body):
        if alias == "/v1/user/device/list":
            return GatewayReply(200, 0, "", {"applianceList": [
                {"applianceCode": 111, "name": "客厅空调", "type": "AC", "online": "1"},
                {"applianceCode": 222, "name": "卧室灯", "type": "LIGHT", "online": "0"},
            ]})
        if alias == "/v1/appliance/thing/template/get":
            return GatewayReply(200, 0, "", {"list": [
                {"applianceCode": 111, "props": [{"code": "power", "type": "switch"}]},
            ]})
        raise AssertionError(f"unexpected alias {alias}")

    client, calls = make(monkeypatch, responder)
    devices = client.devices()

    assert [d["applianceCode"] for d in devices] == [111, 222]
    assert devices[0]["schemaSource"] == "template"
    assert devices[0]["schema"]["props"][0]["code"] == "power"
    # 222 没有模板条目 -> 兜底
    assert devices[1]["schemaSource"] == "fallback"
    assert devices[1]["schema"] == FALLBACK_SCHEMA
    assert devices[1]["online"] is False
    assert calls[0][0] == "/v1/user/device/list"


def test_devices_falls_back_when_template_fails(monkeypatch):
    def responder(alias, body):
        if alias == "/v1/user/device/list":
            return GatewayReply(200, 0, "", {"applianceList": [{"applianceCode": 7, "name": "x"}]})
        return GatewayReply(200, 40002, "token invalid", None)

    client, _ = make(monkeypatch, responder)
    devices = client.devices()
    assert devices[0]["schemaSource"] == "fallback"


def test_devices_raises_on_list_error(monkeypatch):
    client, _ = make(monkeypatch, lambda a, b: GatewayReply(200, 40002, "token invalid", None))
    with pytest.raises(ValueError, match="device list failed"):
        client.devices()


def test_write_props_forwards_payload(monkeypatch):
    def responder(alias, body):
        assert alias == "/v1/thing/batch/properties"
        assert body["iotData"]["applianceCode"] == 111
        assert body["iotData"]["props"] == [{"code": "power", "value": "on"}]
        return GatewayReply(200, 0, "", None)

    client, _ = make(monkeypatch, responder)
    client.write_props(111, [{"code": "power", "value": "on"}])


def test_write_props_raises_on_business_error(monkeypatch):
    client, _ = make(monkeypatch, lambda a, b: GatewayReply(200, 1100, "denied", None))
    with pytest.raises(ValueError, match="set properties failed"):
        client.write_props(111, [{"code": "power", "value": "on"}])


def test_client_none_without_token(monkeypatch):
    monkeypatch.setattr(real_devices, "_override_token", "")
    from app.config import settings

    monkeypatch.setattr(settings, "midea_access_token", "")
    assert real_devices.client() is None


def test_client_built_after_token_injection(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "midea_secret", "prod_secret123@muc")
    real_devices.set_override_token("tok-abc")
    try:
        client = real_devices.client()
        assert client is not None
        assert client.access_token == "tok-abc"
        assert real_devices.active_token() == "tok-abc"
    finally:
        real_devices.set_override_token("")
