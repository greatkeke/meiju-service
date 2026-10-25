"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .domain import SessionState
from .store import store

security = HTTPBearer(auto_error=False)


def auth_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> SessionState:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    item = store.get_by_access_token(credentials.credentials)
    if not item:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid access token")
    return item
