"""
auth.py — JWT-based authentication for Auto Analyst.

Users are identified by a JWT bearer token. For demo purposes, a built-in
user store is used; in production, replace `DEMO_USERS` with a real DB lookup.
Secrets are loaded from environment variables only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

# ── Password hashing ──────────────────────────────────────────────────────────
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Demo user store ───────────────────────────────────────────────────────────
# In production replace with DB queries. Passwords are bcrypt-hashed.
# Generate a hash: from passlib.context import CryptContext; CryptContext(schemes=['bcrypt']).hash('secret')
DEMO_USERS: dict[str, dict] = {
    "admin": {
        "hashed_password": _pwd_context.hash("admin123"),
        "user_id": "u_admin",
        "roles": ["analyst", "admin"],
    },
    "analyst": {
        "hashed_password": _pwd_context.hash("analyst123"),
        "user_id": "u_analyst",
        "roles": ["analyst"],
    },
}

_http_bearer = HTTPBearer(auto_error=True)


# ── Token creation ────────────────────────────────────────────────────────────

def create_access_token(
    data: dict,
    settings: Settings,
    expires_delta: Optional[timedelta] = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


# ── Token verification ────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def authenticate_user(username: str, password: str) -> Optional[dict]:
    user = DEMO_USERS.get(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return {"username": username, **user}


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_http_bearer),
    settings: Settings = Depends(get_settings),
) -> dict:
    """FastAPI dependency — validates JWT and returns user context dict."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        username: str = payload.get("sub", "")
        if not username:
            raise credentials_exception
    except JWTError:
        logger.warning("JWT validation failed")
        raise credentials_exception

    user = DEMO_USERS.get(username)
    if not user:
        raise credentials_exception

    return {"username": username, "user_id": user["user_id"], "roles": user["roles"]}
