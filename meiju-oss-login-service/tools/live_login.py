"""End-to-end live login demo against the Midea MAS gateway.

    python tools/live_login.py sms <mobile>
    python tools/live_login.py verify <mobile> <sms_code>

The MAS secret is read from the MIDEA_SECRET environment variable.
Only use this with an account you own.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.midea import MideaProvider  # noqa: E402

DEFAULT_SECRET = "prod_secret123@muc"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    action, mobile = sys.argv[1], sys.argv[2]
    import os

    secret = os.environ.get("MIDEA_SECRET", DEFAULT_SECRET)
    provider = MideaProvider(secret=secret)

    if action == "sms":
        result = provider.request_sms(mobile)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if action == "verify":
        if len(sys.argv) < 4:
            print("verify needs <sms_code>")
            return 2
        session = provider.verify_sms(mobile, sys.argv[3], None)
        print(json.dumps({
            "user_id": session.user_id,
            "mobile": session.mobile,
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"unknown action: {action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
