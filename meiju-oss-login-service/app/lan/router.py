"""LAN discovery / status / control endpoints, mounted into the main FastAPI app.

统一后端：不再单独跑 8081，所有接口都在同一个服务里。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .service import (
    cache_age_seconds,
    discover_all,
    fetch_all_keys,
    load_cache,
    load_keys,
)

router = APIRouter(tags=["lan"])


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
        return {
            "device_id": device_id,
            "class": type(device).__name__,
            "available": getattr(device, "available", None),
            "attributes": {
                name: device.get_attribute(name)
                for name in sorted(getattr(device, "attributes", {}) or {})
            },
        }
    finally:
        await device.close()


@router.post("/lan/devices/{device_id}/control")
async def device_control(device_id: str, payload: ControlRequest) -> dict[str, Any]:
    from .service import _open_with_keys

    try:
        device = await _open_with_keys(device_id, None)
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        before = device.get_attribute(payload.attribute)
        device.set_attribute(payload.attribute, payload.value)
        return {
            "device_id": device_id,
            "attribute": payload.attribute,
            "before": before,
            "sent": payload.value,
        }
    finally:
        await device.close()


# 供 main app 一次性挂载
def mount(app) -> None:
    app.include_router(router)


__all__ = ["router", "mount", "load_keys"]
