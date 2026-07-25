"""Password hashing (bcrypt) and JWT issuance/verification for role-based
login (patient vs doctor). Replaces the old doctor-only PBKDF2 gate now that
both roles authenticate through the shared `users` table.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException

load_dotenv()

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-insecure-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_MINUTES = 60 * 24 * 7  # 1 week — fine for a demo app

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")


async def verify_supabase_token(access_token: str) -> dict[str, Any]:
    """Verifies a Supabase-issued access token by asking Supabase's own
    /auth/v1/user endpoint who it belongs to. This avoids needing the
    project's JWT secret in this service — Supabase does the verification
    and simply 401s if the token is invalid or expired.
    """
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(status_code=500, detail="Supabase is not configured on the server")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {access_token}", "apikey": SUPABASE_ANON_KEY},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired Google session")
    return resp.json()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, stored: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int, role: str, linked_id: int, name: str) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "linked_id": linked_id,
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRES_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


async def get_current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """FastAPI dependency: extracts and verifies the Bearer JWT, returning
    its claims. Every route that shouldn't be spoofable (chat, notifications,
    session history, ...) depends on this instead of trusting client-supplied
    role/user_ref fields.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def require_doctor(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """FastAPI dependency: same as get_current_user but 403s non-doctors."""
    if user.get("role") != "doctor":
        raise HTTPException(status_code=403, detail="Doctor role required")
    return user
