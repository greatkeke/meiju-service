from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionState(BaseModel):
    user_id: str
    mobile: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    refresh_expires_at: datetime
    login_at: datetime = Field(default_factory=utc_now)


class SmsRequest(BaseModel):
    mobile: str
    purpose: str = "login"


class SmsResponse(BaseModel):
    request_id: str
    mobile: str
    ttl_seconds: int


class SmsLoginRequest(BaseModel):
    mobile: str
    sms_code: str
    device_id: str | None = None


class PasswordLoginRequest(BaseModel):
    """Mirrors the APK's passwordLogin(loginId, password) inputs.

    `mobile` doubles as the account name for local/demo accounts; real upstream
    additionally requires a server-issued `loginId`.
    """

    mobile: str
    password: str
    login_id: str | None = None


class PasswordResetRequest(BaseModel):
    """短信验证后重置密码并直接登录（会改动账号密码）。"""

    mobile: str
    password: str
    sms_code: str


class LoginResponse(BaseModel):
    user_id: str
    mobile: str
    access_token: str
    refresh_token: str
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    user_id: str
    mobile: str
    login_at: datetime


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def new_token(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}{uuid4().hex}"
