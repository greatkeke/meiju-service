"""下载设备 lua 协议脚本（控制字段的完整定义在里面）。

用法（凭据只走环境变量）：
    $env:MEIJU_ACCOUNT="..."; $env:MEIJU_PASSWORD="..."
    uv run --no-sync python tools/fetch_lua.py [device_id ...]

产物：var/lua/<device_id>-<type>.lua
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from aiohttp import ClientSession

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))

from midealocal.cloud import MeijuCloud  # noqa: E402

LUA_DIR = ROOT / "var" / "lua"
KEYS_CACHE = ROOT / "var" / "lan_keys.json"


def credentials() -> tuple[str, str]:
    account = os.environ.get("MEIJU_ACCOUNT", "").strip()
    password = os.environ.get("MEIJU_PASSWORD", "")
    if not account or not password:
        raise SystemExit('请设置 MEIJU_ACCOUNT / MEIJU_PASSWORD 环境变量')
    return account, password


async def main() -> None:
    account, password = credentials()
    LUA_DIR.mkdir(parents=True, exist_ok=True)

    cache = json.loads(KEYS_CACHE.read_text(encoding="utf-8")) if KEYS_CACHE.exists() else {}
    devices = cache.get("devices") or cache
    wanted = sys.argv[1:]

    async with ClientSession() as session:
        cloud = MeijuCloud(
            cloud_name="美的美居", session=session, account=account, password=password
        )
        if not await cloud.login():
            raise SystemExit("云端登录失败")

        for device_id, entry in devices.items():
            if wanted and str(device_id) not in wanted:
                continue
            device_type = entry.get("type")
            sn = entry.get("sn")
            name = entry.get("name")
            if device_type is None or not sn:
                continue
            try:
                device_type_int = int(str(device_type), 16) if isinstance(device_type, str) else int(device_type)
            except (TypeError, ValueError):
                continue
            target_dir = LUA_DIR / f"{device_id}-0x{device_type_int:02X}"
            target_dir.mkdir(parents=True, exist_ok=True)
            existing = list(target_dir.glob("*.lua"))
            if existing:
                print(f"[skip] {name} 已有 {existing[0].name}")
                continue
            try:
                saved = await cloud.download_lua(
                    str(target_dir),
                    device_type_int,
                    str(sn),
                    str(entry.get("model") or "") or None,
                    "0000",
                )
                produced = list(target_dir.glob("*.lua"))
                detail = produced[0].name if produced else (saved or "?")
                size = produced[0].stat().st_size if produced else 0
                print(f"[lua] {name} (0x{device_type_int:02X}) -> {detail} ({size} bytes)")
            except Exception as exc:  # noqa: BLE001
                print(f"[lua] {name} 失败: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
