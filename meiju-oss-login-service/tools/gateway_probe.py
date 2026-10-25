"""Manual gateway probe for the Midea MAS proxy.

This tool is for protocol study against a user's own account and only performs the
single request described on the command line. It never loops, never retries, and
never fabricates an account identity.

Examples
--------
python tools/gateway_probe.py --alias /mj/user/getSmsCode
python tools/gateway_probe.py --alias /mj/user/getSmsCode --method POST --body '{}'
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASES = {
    "prod": "https://mp-prod.smartmidea.net/mas/v5/app/proxy",
    "sit": "https://mp-sit.smartmidea.net/mas/v5/app/proxy",
    "dev": "https://mp-dev.smartmidea.net/mas/v5/app/proxy",
}

DEFAULT_HEADERS = {
    "User-Agent": "okhttp/3.12.0",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/json; charset=utf-8",
}


def build_opener() -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()
    # The app pins/accepts the public certificate chain; verification is disabled here
    # only so a local probe does not depend on the host trust store.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(CookieJar()),
    )


def mas_sign(secret: str, body: str, path_params: dict[str, str], random: str) -> str:
    """Reproduce MideaMasSignPolicy.sign.

    content = (json body) + concat(sorted path "k=v" with "=" stripped)
    sign    = md5(secret + content + random)   -> lowercase hex
    """
    parts = []
    for key in sorted(path_params):
        parts.append(key + path_params[key])
    content = body + "".join(parts)
    return hashlib.md5((secret + content + random).encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe one MAS proxy request")
    parser.add_argument("--base", default="prod", choices=sorted(BASES))
    parser.add_argument("--alias", required=True, help="e.g. /mj/user/getSmsCode")
    parser.add_argument("--method", default="GET", choices=["GET", "POST", "PUT"])
    parser.add_argument("--body", default=None, help="raw JSON body for POST/PUT")
    parser.add_argument("--body-file", default=None, help="read the JSON body from a file (avoids shell quoting)")
    parser.add_argument("--body-b64", default=None, help="base64-encoded JSON body")
    parser.add_argument("--header", action="append", default=[], metavar="K:V")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--secret", default=None, help="sign as MAS with this secret")
    parser.add_argument("--random", default=None, help="override the random nonce")
    parser.add_argument("--param", action="append", default=[], metavar="K:V",
                        help="extra path/query parameter included in the signature")
    args = parser.parse_args()

    url = BASES[args.base] + "?alias=" + urllib.parse.quote(args.alias, safe="/?=&")
    headers = dict(DEFAULT_HEADERS)
    for item in args.header:
        key, _, value = item.partition(":")
        headers[key.strip()] = value.strip()

    path_params = {}
    for item in args.param:
        key, _, value = item.partition(":")
        path_params[key.strip()] = value.strip()
        url += f"&{urllib.parse.quote(key.strip())}={urllib.parse.quote(value.strip())}"

    if args.body_b64:
        import base64 as _b64

        args.body = _b64.b64decode(args.body_b64).decode("utf-8")
    elif args.body_file:
        args.body = Path(args.body_file).read_text(encoding="utf-8")
    if args.secret is not None:
        random = args.random or str(int(time.time() * 1000))
        signature = mas_sign(args.secret, args.body or "", path_params, random)
        headers["sign"] = signature
        headers["random"] = random
        headers["secret"] = args.secret
        print(f"-> random {random}")
        print(f"-> secret {args.secret!r}")
        print(f"-> sign   {signature}")

    data = None
    if args.body is not None:
        data = args.body.encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=args.method)
    print(f"-> {args.method} {url}")
    if args.body is not None:
        print(f"-> body {args.body}")

    try:
        with build_opener().open(request, timeout=args.timeout) as response:
            payload = response.read()
            print(f"<- {response.status}")
            for key in ("Content-Type", "Set-Cookie", "Server"):
                if key in response.headers:
                    print(f"<- {key}: {response.headers[key]}")
            print(payload.decode("utf-8", "replace")[:4000])
            return 0
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        print(f"<- HTTP {exc.code}")
        print(payload.decode("utf-8", "replace")[:4000])
        return 0
    except Exception as exc:  # noqa: BLE001 - probe reports the failure verbatim
        print(f"<- EXC {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

