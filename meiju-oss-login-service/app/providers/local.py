from __future__ import annotations

from datetime import timedelta

from ..config import settings
from ..domain import new_id, new_token, utc_now
from ..store import store
from .base import LoginProvider, UpstreamSession


class LocalProvider(LoginProvider):
    """Open-source default provider. It deliberately never contacts Midea servers."""

    def __init__(self) -> None:
        self._last_codes: dict[str, str] = {}

    def request_sms(self, mobile: str) -> None:
        code = "123456" if settings.debug_local_codes else new_id("code")[-6:]
        self._last_codes[mobile] = code
        store.create_sms_challenge(mobile, code, ttl_seconds=300)

    def verify_sms(self, mobile: str, sms_code: str, device_id: str | None) -> UpstreamSession:
        challenge = store.get_sms_challenge(mobile)
        if not challenge or challenge[0] != sms_code:
            raise ValueError("invalid sms code")
        store.clear_sms_challenge(mobile)
        return self._session(mobile)

    def password_login(self, mobile: str, password: str, login_id: str | None = None) -> UpstreamSession:
        """Local demo password login: any account with the configured password.

        Password comes from the LOCAL_PASSWORD env var (default 123456).
        The real upstream needs loginId + IOT_KEY, see providers/midea.py.
        """
        if password != settings.local_password:
            raise ValueError("密码错误（本地演示密码由 LOCAL_PASSWORD 环境变量配置，默认 123456）")
        return self._session(mobile)

    def _session(self, mobile: str) -> UpstreamSession:
        now = utc_now()
        return UpstreamSession(
            user_id=new_id("local_user"),
            mobile=mobile,
            access_token=new_token("local_access"),
            refresh_token=new_token("local_refresh"),
            expires_at=now + timedelta(seconds=settings.access_token_ttl_seconds),
            refresh_expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )

    def refresh(self, refresh_token: str) -> UpstreamSession:
        item = store.get_by_refresh_token(refresh_token)
        if not item:
            raise ValueError("invalid refresh token")
        now = utc_now()
        return UpstreamSession(
            user_id=item.user_id,
            mobile=item.mobile,
            access_token=new_token("local_access"),
            refresh_token=new_token("local_refresh"),
            expires_at=now + timedelta(seconds=settings.access_token_ttl_seconds),
            refresh_expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )

    def logout(self, access_token: str) -> None:
        store.delete_session(access_token)

    def last_code(self, mobile: str) -> str | None:
        return self._last_codes.get(mobile)


local_provider = LocalProvider()
