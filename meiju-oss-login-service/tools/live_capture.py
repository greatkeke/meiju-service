"""Live capture client: every request/response pair is written to disk before analysis.

Records land in probes/captures/<timestamp>-<label>.json and contain the exact request
(method, url, headers, body) plus the raw response (status, headers, body), so later
analysis never depends on what a terminal printed.

Commands
--------
python tools/live_capture.py sms     <mobile>
python tools/live_capture.py verify  <mobile> <smsCode>
python tools/live_capture.py token   <mobile> <randomCode>
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mas import sign_headers  # noqa: E402

BASE = "https://mp-prod.smartmidea.net/mas/v5/app/proxy"
SECRET = "prod_secret123@muc"
APP_VERSION = "11.5.11.4"
DEVICE_ID = "dsh-capture-0001"

CAPTURE_DIR = Path(__file__).resolve().parents[1] / "probes" / "captures"
# Never persist full credentials in the clear: tokens are redacted in the record.
REDACT_HEADERS = {"accessToken", "secret"}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def envelope(mobile: str, iot: dict) -> dict:
    """CommonReqData wrapper that the middle platform requires."""
    stamp = _stamp()
    return {
        "data": {
            "appKey": "",
            "appVersion": APP_VERSION,
            "deviceId": DEVICE_ID,
            "deviceName": "DSH Capture",
            "mobile": mobile,
            "osVersion": "13",
            "platform": 0,
        },
        "iotData": {"mobile": mobile, "reqId": f"req{int(time.time() * 1000)}", "stamp": stamp, **iot},
        "timestamp": stamp,
    }


def post(alias: str, body: dict, label: str, token: str | None = None) -> dict:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Encoding": "identity",
        "User-Agent": "okhttp/3.12.0",
        "version": APP_VERSION,
        "platform": "0",
    }
    if token:
        headers["accessToken"] = token
    headers.update(sign_headers(SECRET, raw))
    request = urllib.request.Request(
        f"{BASE}?alias={alias}", data=raw.encode("utf-8"), headers=headers, method="POST"
    )

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    record: dict = {
        "label": label,
        "capturedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request": {
            "method": "POST",
            "url": f"{BASE}?alias={alias}",
            "alias": alias,
            "headers": {k: ("<redacted>" if k in REDACT_HEADERS else v) for k, v in headers.items()},
            "bodyRaw": raw,
            "body": body,
        },
    }

    try:
        with opener.open(request, timeout=30) as response:
            text = response.read().decode("utf-8", "replace")
            record["response"] = {
                "httpStatus": response.status,
                "headers": dict(response.headers),
                "bodyRaw": text,
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        record["response"] = {"httpStatus": exc.code, "headers": dict(exc.headers or {}), "bodyRaw": text}
    except Exception as exc:  # noqa: BLE001 - transport failures are data too
        record["response"] = {"error": f"{type(exc).__name__}: {exc}"}
        text = ""

    try:
        record["response"]["json"] = json.loads(text) if text else None
    except json.JSONDecodeError:
        record["response"]["json"] = None

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = CAPTURE_DIR / f"{stamp}-{label}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{label}] {alias}")
    print(f"  -> {record['response'].get('httpStatus', record['response'])} {text[:400]}")
    print(f"  saved {path}")
    return record


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    action, mobile = sys.argv[1], sys.argv[2]

    if action == "sms":
        post("/mj/user/getSmsCode", envelope(mobile, {"type": 2}), "getSmsCode")
        return 0

    if action == "verify":
        if len(sys.argv) < 4:
            print("verify needs <smsCode>")
            return 2
        post("/mj/user/mobileVerify", envelope(mobile, {"smsCode": sys.argv[3], "type": 2}), "mobileVerify")
        return 0

    if action == "token":
        if len(sys.argv) < 4:
            print("token needs <randomCode>")
            return 2
        token = sys.argv[3]
        # Matrix tried inside one randomCode lifetime: no extra SMS is spent here.
        attempts = [
            ("/v6/mj/user/login", {"loginToken": token, "type": 2, "clientType": 1}),
            ("/v6/mj/user/login", {"loginToken": token, "type": 2, "clientType": 1, "iampwd": "", "password": ""}),
            ("/v6/mj/user/login", {"loginToken": token, "loginAccount": mobile, "type": 2}),
            ("/mj/user/autoLogin", {"tokenPwd": token, "type": 2}),
            ("/mj/user/autoLogin", {"loginToken": token, "type": 2}),
            ("/v6/mj/user/onekey/auth", {"loginToken": token, "jPushAppCode": 0, "type": 2}),
            ("/v1/user/login/id/get", {"loginToken": token, "type": 2}),
            ("/mj/user/mobileVerify", {"loginToken": token, "type": 2, "smsCode": token}),
        ]
        for index, (alias, iot) in enumerate(attempts, start=1):
            body = envelope(mobile, iot)
            # MucData also carries loginToken on some flows; try it as a variant.
            if index == 3:
                body["data"]["loginToken"] = token
            post(alias, body, f"token-{index:02d}")
            time.sleep(0.4)
        return 0

    print(f"unknown action: {action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
