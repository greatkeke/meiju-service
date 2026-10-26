"""LAN discovery / status / control endpoints, mounted into the main FastAPI app.

统一后端：不再单独跑 8081，所有接口都在同一个服务里。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# 先导入 service：它会把 vendor/ 加入 sys.path，之后才能导入 midealocal
from .service import (
    cache_age_seconds,
    discover_all,
    fetch_all_keys,
    load_cache,
    load_keys,
)

from midealocal.exceptions import MideaLocalError  # noqa: E402

router = APIRouter(tags=["lan"])

# 已从各机型 set_attribute 实现中确认的可下发属性（仅用于给出准确报错）。
# 未列出的机型改用「是否真的构建了报文」来判定。
SETTABLE_HINTS: dict[int, set[str]] = {
    0xDC: {"power", "start", "ai_switch"},  # 干衣机
    0xDA: {"power", "start"},
    0xDB: {"power", "start"},
    0xB6: {"power", "mode", "fan_speed", "light"},  # 抽油烟机
    0xE3: {"power", "target_temperature"},  # 燃气热水器
    0xE2: {"power", "target_temperature"},
    0xA1: {"fan_speed", "mode", "prompt_tone", "water_level_set"},
    0xC2: {"power"},
    0xCE: {"mode"},
    0xFA: {"fan_speed", "mode", "scene", "voice"},
    0xFB: {"humidity_mode", "mode"},
}


class LoginRequest(BaseModel):
    account: str
    password: str


class ControlRequest(BaseModel):
    attribute: str
    value: Any


@router.get("/lan/health")
def lan_health() -> dict[str, Any]:
    cache = load_cache()
    return {
        "ok": True,
        "cached_devices": len(cache.get("devices") or {}),
        "cache_valid": bool(cache.get("valid")),
        "fetched_at": cache.get("fetched_at"),
        "cache_age_seconds": cache_age_seconds(cache),
        "account": cache.get("account"),
    }


@router.post("/lan/keys/login")
async def keys_login(payload: LoginRequest) -> dict[str, Any]:
    """用美居账号密码登录并缓存设备密钥。密码只在内存里用于本次登录。"""
    account = payload.account.strip()
    if not account or not payload.password:
        raise HTTPException(status_code=400, detail="account / password 不能为空")
    try:
        cache = await fetch_all_keys(account, payload.password)
    except SystemExit as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"云端登录异常: {exc}") from exc
    return {
        "ok": True,
        "account": cache.get("account"),
        "fetched_at": cache.get("fetched_at"),
        "count": len(cache.get("devices") or {}),
    }


@router.get("/lan/keys")
def lan_keys() -> dict[str, Any]:
    cache = load_cache()
    devices = cache.get("devices") or {}
    return {
        "ok": bool(devices) and bool(cache.get("valid")),
        "valid": bool(cache.get("valid")),
        "count": len(devices),
        "account": cache.get("account"),
        "fetched_at": cache.get("fetched_at"),
        "age_seconds": cache_age_seconds(cache),
        "invalid_reason": cache.get("invalid_reason"),
        "devices": {
            device_id: {
                "name": entry.get("name"),
                "type": entry.get("type"),
                "sn": entry.get("sn"),
                "methods": sorted((entry.get("keys") or {}).keys()),
            }
            for device_id, entry in devices.items()
        },
    }


@router.get("/lan/devices")
def lan_cached_devices() -> dict[str, Any]:
    cache = load_cache()
    devices = []
    for device_id, entry in (cache.get("devices") or {}).items():
        raw_type = entry.get("type")
        try:
            type_hex = (
                f"0x{int(str(raw_type), 16):02X}"
                if isinstance(raw_type, str)
                else f"0x{int(raw_type):02X}"
            )
        except (TypeError, ValueError):
            type_hex = str(raw_type)
        devices.append(
            {
                "device_id": device_id,
                "name": entry.get("name"),
                "type": type_hex,
                "sn": entry.get("sn"),
                "model": entry.get("model"),
                "methods": sorted((entry.get("keys") or {}).keys()),
                "has_keys": bool(entry.get("keys")),
            }
        )
    devices.sort(key=lambda d: (d["type"], str(d["name"])))
    return {"count": len(devices), "devices": devices}


@router.get("/lan/subnets")
def lan_subnets() -> dict[str, Any]:
    """本次会扫描哪些网段，以及它们是怎么推断出来的。"""
    import os

    from .subnets import detect_cidrs, resolve_cidrs

    return {
        "scan": resolve_cidrs(),
        "detected": detect_cidrs(),
        "env_override": os.environ.get("MEIJU_LAN_CIDR"),
        "note": "MEIJU_LAN_CIDR 优先级最高；否则按 缓存设备网段 → 本机网卡 → 路由表 推断",
    }


@router.post("/lan/warmup")
async def lan_warmup(cidr: str | None = None) -> dict[str, Any]:
    """手动触发发现并刷新地址缓存（服务启动时也会自动跑一次）。"""
    from .service import save_endpoints

    found = await discover_all(cidr=cidr)
    save_endpoints(found)
    return {
        "count": len(found),
        "devices": [
            {
                "device_id": did,
                "sn": info.get("sn"),
                "ip": info.get("ip_address"),
                "type": f"0x{int(info.get('type') or 0):02X}",
            }
            for did, info in found.items()
        ],
    }


@router.get("/lan/discover")
async def lan_discover(cidr: str | None = None) -> dict[str, Any]:
    found = await discover_all(cidr=cidr)
    cache = load_cache()
    by_sn = {
        str(entry.get("sn") or ""): (cid, entry)
        for cid, entry in (cache.get("devices") or {}).items()
    }
    devices = []
    for device_id, info in found.items():
        sn = str(info.get("sn") or "")
        cached_id, entry = by_sn.get(sn, (None, None))
        devices.append(
            {
                "device_id": device_id,
                "name": (entry or {}).get("name"),
                "cloud_id": cached_id,
                "type": f"0x{int(info.get('type') or 0):02X}",
                "ip": info.get("ip_address"),
                "port": info.get("port"),
                "protocol": info.get("protocol"),
                "sn": sn,
                "model": info.get("model"),
                "has_keys": cached_id is not None,
            }
        )
    return {
        "count": len(devices),
        "devices": devices,
        "note": (
            "0 台表示本机与设备不在同一局域网（广播穿不过三层隧道时会自动改用单播扫网段）"
            if not devices
            else "cloud_id 为云端设备码（按 SN 匹配），用它读取状态/下发控制"
        ),
    }


@router.get("/lan/devices/{device_id}/status")
async def device_status(device_id: str) -> dict[str, Any]:
    from .service import _open_with_keys

    try:
        device = await _open_with_keys(device_id, None)
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        model_type = getattr(device, "_meiju_type", None)
        hints = SETTABLE_HINTS.get(model_type or -1)
        return {
            "device_id": device_id,
            "class": type(device).__name__,
            "type": f"0x{(model_type or 0):02X}",
            "available": getattr(device, "available", None),
            # 定位方式与耗时：cached=直连缓存地址（快），sweep=扫描后发现（Tailscale 下很慢）
            "source": getattr(device, "_meiju_source", None),
            "elapsed_ms": getattr(device, "_meiju_elapsed_ms", None),
            # 该机型可下发的属性；None 表示未收录，前端应展示全部属性但提示可能只读
            "settable": sorted(hints) if hints else None,
            "attributes": {
                name: device.get_attribute(name)
                for name in sorted(getattr(device, "attributes", {}) or {})
            },
        }
    finally:
        await device.close()


@router.post("/lan/devices/{device_id}/control")
async def device_control(device_id: str, payload: ControlRequest) -> dict[str, Any]:
    """下发控制并**确认结果**。

    midea-local 各机型的 `set_attribute` 对不支持的属性会静默返回（不抛异常、
    也不发报文），所以这里做了两件事：
      1. 统计本次调用是否真的构建/发送了报文 —— 没有则明确报错；
      2. 发送后等待并重新读取，未生效则返回 409 而不是假装成功。
    """
    import asyncio

    from .service import _open_with_keys

    try:
        device = await _open_with_keys(device_id, None)
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        attr = payload.attribute.strip()
        before = device.get_attribute(attr)
        if before is None and attr not in (getattr(device, "attributes", {}) or {}):
            available = ", ".join(sorted(getattr(device, "attributes", {}) or {}))
            raise HTTPException(
                status_code=400,
                detail=f"设备没有属性 `{attr}`。可用属性：{available}",
            )

        value = payload.value
        # 布尔型属性：把 "true"/"1"/"on" 之类的输入规范化
        if isinstance(before, bool) and not isinstance(value, bool):
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "on", "yes")
            elif isinstance(value, (int, float)):
                value = bool(value)

        # 统计是否真的发出了报文
        sent = {"count": 0}
        original_build_send = device.build_send

        def counting_build_send(cmd):  # type: ignore[no-untyped-def]
            sent["count"] += 1
            return original_build_send(cmd)

        device.build_send = counting_build_send  # type: ignore[method-assign]
        try:
            device.set_attribute(attr, value)
        except MideaLocalError as exc:
            model_type = getattr(device, "_meiju_type", None)
            hints = SETTABLE_HINTS.get(model_type or -1)
            if hints and attr not in hints:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"`{attr}` 不是该机型的可下发属性（0x{(model_type or 0):02X}），"
                        f"它只能下发：{', '.join(sorted(hints))}"
                    ),
                ) from exc
            raise HTTPException(
                status_code=400,
                detail=f"下发参数不合法：{exc}（该属性可能只读，或需要不同的值类型）",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"构造指令失败：{exc}") from exc
        finally:
            device.build_send = original_build_send  # type: ignore[method-assign]

        if sent["count"] == 0:
            model_type = getattr(device, "_meiju_type", None)
            hints = SETTABLE_HINTS.get(model_type or -1)
            hint_text = f"该机型只能下发：{', '.join(sorted(hints))}" if hints else "该机型未实现此属性的下发"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"机型 {type(device).__name__} 未实现属性 `{attr}` 的下发（没有产生任何指令），"
                    f"设备不会响应。{hint_text}。"
                ),
            )

        # 等待设备生效并读回确认
        await asyncio.sleep(2.0)
        await device.refresh_status()
        await asyncio.sleep(1.5)
        after = device.get_attribute(attr)
        confirmed = after == value or (
            isinstance(value, bool) and isinstance(after, bool) and after == value
        )
        if not confirmed:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"指令已发送，但设备未确认变更（{attr}: {before!r} -> {after!r}）。"
                    "可能被设备拒绝、当前模式不允许，或设备未在线。"
                ),
            )
        return {
            "device_id": device_id,
            "attribute": attr,
            "before": before,
            "sent": value,
            "after": after,
            "confirmed": True,
            "source": getattr(device, "_meiju_source", None),
            "elapsed_ms": getattr(device, "_meiju_elapsed_ms", None),
        }
    finally:
        await device.close()


# 供 main app 一次性挂载
def mount(app) -> None:
    app.include_router(router)


__all__ = ["router", "mount", "load_keys"]
