from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .deps import auth_session
from .device_api import router as device_router
from .lan.router import router as lan_router
from .domain import (
    LoginResponse,
    PasswordLoginRequest,
    PasswordResetRequest,
    RefreshRequest,
    SessionState,
    SmsLoginRequest,
    SmsRequest,
    SmsResponse,
    UserResponse,
    new_id,
    utc_now,
)
from .providers.local import local_provider
from .providers.midea import GatewayError, MideaProvider
from .store import store

app = FastAPI(title="Meiju OSS Login Service", version="0.1.0")

# 默认放行任意来源（本地演示用，鉴权靠 Bearer 头而非 Cookie）。
# 需要收紧时设置环境变量 CORS_ORIGINS="http://a,http://b"。
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if "*" not in _cors_origins else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(device_router)
app.include_router(lan_router)


@app.get("/health")
def health() -> dict[str, object]:
    from . import real_devices

    return {
        "ok": True,
        "provider": settings.provider,
        "version": "0.1.0",
        "device_mode": "real" if real_devices.active_token() else "simulated",
    }


@app.post("/v1/session/real-token")
def set_real_token(payload: dict) -> dict[str, object]:
    """注入官方 App 的 accessToken，之后设备接口直接转发到美的云端。

    token 只存在进程内存，重启失效；也可用 MIDEA_ACCESS_TOKEN 环境变量。
    """
    from . import real_devices

    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="access_token must not be empty")
    real_devices.set_override_token(token)
    return {"ok": True, "device_mode": "real"}


def provider():
    if settings.provider == "local":
        return local_provider
    if settings.provider == "midea":
        try:
            return MideaProvider(
                secret=settings.midea_secret,
                base_url=settings.midea_base,
                device_id=settings.midea_device_id,
                iot_key=settings.midea_iot_key,
            )
        except ValueError as exc:  # 缺 MIDEA_SECRET 之类的配置问题 → 501 而非 500
            raise HTTPException(status_code=501, detail=str(exc)) from exc
    raise HTTPException(status_code=501, detail="configured provider is not available")


def _issue(upstream) -> LoginResponse:
    """Persist the upstream session and shape the public response."""
    session = SessionState(
        user_id=upstream.user_id,
        mobile=upstream.mobile,
        access_token=upstream.access_token,
        refresh_token=upstream.refresh_token,
        expires_at=upstream.expires_at,
        refresh_expires_at=upstream.refresh_expires_at,
        login_at=utc_now(),
    )
    store.save_session(session)
    return LoginResponse(
        user_id=session.user_id,
        mobile=session.mobile,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=settings.access_token_ttl_seconds,
    )


@app.post("/v1/login/request-sms", response_model=SmsResponse)
def request_sms(payload: SmsRequest, login_provider=Depends(provider)) -> SmsResponse:
    try:
        login_provider.request_sms(payload.mobile)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SmsResponse(request_id=new_id("sms"), mobile=payload.mobile, ttl_seconds=300)


@app.post("/v1/login/verify-sms", response_model=LoginResponse)
def verify_sms(payload: SmsLoginRequest, login_provider=Depends(provider)) -> LoginResponse:
    try:
        upstream = login_provider.verify_sms(payload.mobile, payload.sms_code, payload.device_id)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _issue(upstream)


@app.post("/v1/login/password", response_model=LoginResponse)
def password_login(payload: PasswordLoginRequest, login_provider=Depends(provider)) -> LoginResponse:
    """Password login.

    local  : any account whose password matches LOCAL_PASSWORD (default 123456).
    midea  : posts /v6/mj/user/login with the APK's own crypto; raises 400 with the
             precise missing inputs (loginId / IOT_KEY) until they are recovered.
    """
    try:
        upstream = login_provider.password_login(
            payload.mobile, payload.password, payload.login_id
        )
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return _issue(upstream)


@app.post("/v1/login/reset-password", response_model=LoginResponse)
def reset_password(payload: PasswordResetRequest, login_provider=Depends(provider)) -> LoginResponse:
    """短信验证 → 重置密码 → 立即登录。

    **会改动账号密码**：请在明确知情下调用。真实模式下用于把账号密码重置成
    本地可计算的哈希，从而绕过 native `encodeMD5` 无法复现的问题。
    """
    reset_and_login = getattr(login_provider, "reset_and_login", None)
    if reset_and_login is None:
        raise HTTPException(status_code=501, detail="当前 provider 不支持重置密码登录")
    try:
        upstream = reset_and_login(payload.mobile, payload.password, payload.sms_code)
    except GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _issue(upstream)


@app.post("/v1/login/refresh-token", response_model=LoginResponse)
def refresh_token(payload: RefreshRequest, login_provider=Depends(provider)) -> LoginResponse:
    old = store.get_by_refresh_token(payload.refresh_token)
    if not old:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    try:
        upstream = login_provider.refresh(payload.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_session = SessionState(
        user_id=old.user_id,
        mobile=old.mobile,
        access_token=upstream.access_token,
        refresh_token=upstream.refresh_token,
        expires_at=upstream.expires_at,
        refresh_expires_at=upstream.refresh_expires_at,
        login_at=old.login_at,
    )
    store.replace_session(old, new_session)
    return LoginResponse(
        user_id=new_session.user_id,
        mobile=new_session.mobile,
        access_token=new_session.access_token,
        refresh_token=new_session.refresh_token,
        expires_in=settings.access_token_ttl_seconds,
    )


@app.get("/v1/me", response_model=UserResponse)
def me(session: SessionState = Depends(auth_session)) -> UserResponse:
    return UserResponse(user_id=session.user_id, mobile=session.mobile, login_at=session.login_at)


@app.post("/v1/logout")
def logout(session: SessionState = Depends(auth_session), login_provider=Depends(provider)) -> dict:
    login_provider.logout(session.access_token)
    return {"ok": True}


@app.get("/v1/login/debug/local-code")
def debug_local_code(mobile: str) -> dict:
    if not settings.debug_local_codes:
        raise HTTPException(status_code=404, detail="debug codes disabled")
    return {"mobile": mobile, "code": local_provider.last_code(mobile)}
