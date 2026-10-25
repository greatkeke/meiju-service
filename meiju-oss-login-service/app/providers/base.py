from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class UpstreamSession:
    user_id: str
    mobile: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    refresh_expires_at: datetime


class LoginProvider(ABC):
    @abstractmethod
    def request_sms(self, mobile: str) -> None: ...

    @abstractmethod
    def verify_sms(self, mobile: str, sms_code: str, device_id: str | None) -> UpstreamSession: ...

    @abstractmethod
    def password_login(self, mobile: str, password: str, login_id: str | None = None) -> UpstreamSession: ...

    @abstractmethod
    def refresh(self, refresh_token: str) -> UpstreamSession: ...

    @abstractmethod
    def logout(self, access_token: str) -> None: ...
