"""End-to-end check: login -> device list -> property write -> read back."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def call(method: str, path: str, payload: dict | None = None, token: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def main() -> None:
    print("frontend :", urllib.request.urlopen("http://127.0.0.1:4300/", timeout=10).status)

    status, _ = call("POST", "/v1/login/request-sms", {"mobile": "13800138000"})
    status, code = call("GET", "/v1/login/debug/local-code?mobile=13800138000")
    sms = code["code"]
    status, tokens = call("POST", "/v1/login/verify-sms", {"mobile": "13800138000", "sms_code": sms})
    token = tokens["access_token"]
    print("login    :", status, "token", token[:22] + "…")

    status, devices = call("GET", "/v1/user/device/list", token=token)
    items = devices["data"]["applianceList"]
    print("devices  :", status, len(items), "台")
    for item in items:
        print(f"   {item['applianceCode']} {item['name']:<8} {item['type']:<6} {item['props']}")

    ac = next(d for d in items if d["type"] == "0xAC")
    code_value = ac["applianceCode"]

    status, updated = call(
        "POST",
        "/v1/thing/batch/properties",
        {"applianceCode": code_value, "props": [
            {"code": "power", "value": "on"},
            {"code": "mode", "value": "heat"},
            {"code": "targetTemperature", "value": 28},
            {"code": "fanSpeed", "value": "high"},
        ]},
        token=token,
    )
    print("control  :", status, updated["data"]["props"])

    status, state = call("GET", f"/v1/thing/properties/{code_value}", token=token)
    print("readback :", status, state["data"]["props"])

    status, bad = call(
        "POST", "/v1/thing/batch/properties",
        {"applianceCode": code_value, "props": [{"code": "targetTemperature", "value": 99}]},
        token=token,
    )
    print("reject   :", status, bad.get("detail"))

    status, action = call(
        "POST", f"/v1/thing/action/{code_value}",
        {"method": "setProperties", "params": {"power": "off"}},
        token=token,
    )
    print("action   :", status, action["data"]["props"])


if __name__ == "__main__":
    main()
