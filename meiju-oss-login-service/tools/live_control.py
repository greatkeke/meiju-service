"""Live control client for the real Midea cloud (MAS gateway).

Everything here goes through the same signed proxy the app uses:

    POST https://mp-prod.smartmidea.net/mas/v5/app/proxy?alias=<alias>
    headers: sign / random / secret  + accessToken

Requires an access token from *your own* account. Without one the gateway answers
with an auth error, which is still useful: it proves the route exists and that the
signature is accepted.

Usage:
    python tools/live_control.py devices <accessToken>
    python tools/live_control.py props   <accessToken> <applianceCode>
    python tools/live_control.py set     <accessToken> <applianceCode> power=on mode=cool
    python tools/live_control.py action  <accessToken> <applianceCode> '{"method":"setProperties","params":{"power":"on"}}'
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mas import sign_headers  # noqa: E402

BASE = "https://mp-prod.smartmidea.net/mas/v5/app/proxy"
SECRET = "prod_secret123@muc"
APP_VERSION = "11.5.11.4"


def _stamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def envelope(iot: dict) -> dict:
    """CommonReqData: the middle-platform wrapper the gateway insists on."""
    stamp = _stamp()
    return {"data": {}, "iotData": {**iot, "stamp": stamp}, "timestamp": stamp}


def call(alias: str, body: dict, token: str, method: str = "POST") -> dict:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Encoding": "identity",
        "User-Agent": "okhttp/3.12.0",
        "version": APP_VERSION,
        "platform": "0",
        "accessToken": token,
    }
    headers.update(sign_headers(SECRET, raw))
    request = urllib.request.Request(
        f"{BASE}?alias={alias}", data=raw.encode("utf-8"), headers=headers, method=method
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"httpStatus": exc.code, "body": exc.read().decode("utf-8", "replace")[:400]}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    action, token = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]

    if action == "devices":
        reply = call("/v1/user/device/list", envelope({}), token)
        print(json.dumps(reply, ensure_ascii=False, indent=2)[:2000])
        return 0

    if action == "props":
        code = int(rest[0])
        reply = call(f"/v1/thing/properties/{code}", envelope({"applianceCode": code}), token)
        print(json.dumps(reply, ensure_ascii=False, indent=2)[:2000])
        return 0

    if action == "set":
        code = int(rest[0])
        props = []
        for pair in rest[1:]:
            key, _, value = pair.partition("=")
            text = value
            if text.isdigit():
                text = int(text)
            props.append({"code": key, "value": text})
        body = envelope(
            {
                "applianceCode": code,
                "props": props,
                "disablePropIntegrityCheck": False,
                "disablePropLegalityCheck": False,
            }
        )
        reply = call("/v1/thing/batch/properties", body, token)
        print(json.dumps(reply, ensure_ascii=False, indent=2)[:2000])
        return 0

    if action == "action":
        code = int(rest[0])
        raw = rest[1] if len(rest) > 1 else "{}"
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        payload = json.loads(raw)
        body = envelope({"applianceCode": code, **payload})
        reply = call(f"/v1/thing/action/{code}", body, token)
        print(json.dumps(reply, ensure_ascii=False, indent=2)[:2000])
        return 0

    print(f"unknown action: {action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
