from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from .domain import SessionState, utc_now


class LoginStore(Protocol):
    def create_sms_challenge(self, mobile: str, code: str, ttl_seconds: int) -> None: ...
    def get_sms_challenge(self, mobile: str) -> tuple[str, datetime] | None: ...
    def clear_sms_challenge(self, mobile: str) -> None: ...
    def save_session(self, session: SessionState) -> None: ...
    def get_by_access_token(self, token: str) -> SessionState | None: ...
    def get_by_refresh_token(self, token: str) -> SessionState | None: ...
    def replace_session(self, old: SessionState, new: SessionState) -> None: ...
    def delete_session(self, access_token: str) -> None: ...


class InMemoryLoginStore:
    def __init__(self) -> None:
        self._sms: dict[str, tuple[str, datetime]] = {}
        self._sessions: dict[str, SessionState] = {}
        self._by_refresh: dict[str, str] = {}

    def create_sms_challenge(self, mobile: str, code: str, ttl_seconds: int) -> None:
        self._sms[mobile] = (code, utc_now() + timedelta(seconds=ttl_seconds))

    def get_sms_challenge(self, mobile: str) -> tuple[str, datetime] | None:
        item = self._sms.get(mobile)
        if not item:
            return None
        code, expires_at = item
        if expires_at < utc_now():
            self._sms.pop(mobile, None)
            return None
        return code, expires_at

    def clear_sms_challenge(self, mobile: str) -> None:
        self._sms.pop(mobile, None)

    def save_session(self, session: SessionState) -> None:
        self._sessions[session.access_token] = session
        self._by_refresh[session.refresh_token] = session.access_token

    def get_by_access_token(self, token: str) -> SessionState | None:
        item = self._sessions.get(token)
        if not item:
            return None
        if item.expires_at < utc_now():
            self.delete_session(token)
            return None
        return item

    def get_by_refresh_token(self, token: str) -> SessionState | None:
        access_token = self._by_refresh.get(token)
        if not access_token:
            return None
        item = self._sessions.get(access_token)
        if not item or item.refresh_expires_at < utc_now():
            self.delete_session(access_token)
            return None
        return item

    def replace_session(self, old: SessionState, new: SessionState) -> None:
        self.delete_session(old.access_token)
        self.save_session(new)

    def delete_session(self, access_token: str) -> None:
        old = self._sessions.pop(access_token, None)
        if old:
            self._by_refresh.pop(old.refresh_token, None)


store = InMemoryLoginStore()
