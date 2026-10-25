"""In-process checks for the real-environment provider wiring (no network).

Run with env vars set, e.g.:
    MIDEA_PROVIDER=midea MIDEA_SECRET=... python tools/check_real_mode.py
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
failed: list[str] = []


def check(name: str, ok: bool, detail: object) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} -> {detail}")
    if not ok:
        failed.append(name)


def main() -> None:
    health = client.get("/health").json()
    check(
        "health 报告 provider",
        health.get("provider") == os.environ.get("MIDEA_PROVIDER"),
        health,
    )

    reply = client.post(
        "/v1/login/password",
        json={"mobile": "13800138000", "password": "whatever"},
    )
    body = reply.json()
    if os.environ.get("MIDEA_IOT_KEY"):
        # 注入了 IOT_KEY 说明真实链路齐备；这里故意**不**断言登录结果，
        # 以免把这个自检变成对真实账号的密码尝试。
        check("注入 IOT_KEY 后不再返回 501", reply.status_code != 501, reply.status_code)
    else:
        check(
            "缺 IOT_KEY → 501 且说明原因",
            reply.status_code == 501 and "IOT_KEY" in str(body.get("detail", "")),
            body,
        )

    if failed:
        raise SystemExit(f"failed: {failed}")
    print("real-mode wiring ok")


if __name__ == "__main__":
    main()
