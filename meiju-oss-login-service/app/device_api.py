"""Device control endpoints, shaped after the APK's own routes.

Real aliases recovered from the APK (see docs/protocol-notes.md):
    GET  /v1/user/device/list
    GET  /v1/thing/properties/{applianceCode}
    POST /v1/thing/batch/properties
    POST /v1/thing/action/{applianceCode}
    GET  /v1/thing/batch/history?requestId=...

This module serves the same shapes against the local simulator.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .devices import DeviceError, registry
from .deps import auth_session
from .domain import SessionState
from . import real_devices

router = APIRouter(tags=["device"])


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "msg": "ok", "data": data}


def _real() -> "real_devices.RealDevices | None":
    """注入真实 accessToken 后走美的云端，否则用本地模拟设备。"""
    try:
        return real_devices.client()
    except Exception:  # noqa: BLE001 - 真实层不可用时退回模拟，避免整站 500
        return None


@router.get("/v1/user/device/list")
def device_list(session: SessionState = Depends(auth_session)) -> dict[str, Any]:
    client = _real()
    if client is not None:
        try:
            return _ok({"applianceList": client.devices(), "mode": "real"})
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _ok({"applianceList": [d.to_dict() for d in registry.list()], "mode": "simulated"})


@router.get("/v1/thing/properties/{appliance_code}")
def device_properties(appliance_code: int, session: SessionState = Depends(auth_session)) -> dict[str, Any]:
    client = _real()
    if client is not None:
        try:
            return _ok({"applianceCode": appliance_code, "raw": client.raw_props(appliance_code), "mode": "real"})
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        device = registry.get(appliance_code)
    except DeviceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _ok(device.to_dict())


@router.post("/v1/thing/batch/properties")
def set_batch_properties(payload: dict[str, Any], session: SessionState = Depends(auth_session)) -> dict[str, Any]:
    """Mirror of NewTSLBatchPropertiesReq."""
    code = payload.get("applianceCode")
    props = payload.get("props")
    if code is None:
        raise HTTPException(status_code=400, detail="applianceCode must not be null")
    if not isinstance(props, list):
        raise HTTPException(status_code=400, detail="props must be a non-empty list")

    client = _real()
    if client is not None:
        try:
            data = client.write_props(int(code), props)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _ok({"applianceCode": int(code), "raw": data, "mode": "real"})

    try:
        device = registry.set_props(int(code), props)
    except DeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok(device.to_dict())


@router.post("/v1/thing/action/{appliance_code}")
def device_action(appliance_code: int, payload: dict[str, Any], session: SessionState = Depends(auth_session)) -> dict[str, Any]:
    """Mirror of TSLAction {method, params, url}.

    Supported methods: setProperties (params = {code: value, ...}).
    """
    method = str(payload.get("method") or "").strip()
    params = payload.get("params")
    if not method:
        raise HTTPException(status_code=400, detail="method must not be empty")
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be an object")

    if method not in ("setProperties", "setProperty", "set"):
        raise HTTPException(status_code=400, detail=f"unsupported method: {method}")

    props = [{"code": key, "value": value} for key, value in params.items()]

    client = _real()
    if client is not None:
        try:
            data = client.write_props(appliance_code, props)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _ok({"applianceCode": appliance_code, "raw": data, "method": method, "mode": "real"})

    try:
        device = registry.set_props(appliance_code, props)
    except DeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _ok({"applianceCode": appliance_code, "props": device.props, "method": method})

