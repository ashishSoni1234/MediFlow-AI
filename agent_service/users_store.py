"""Direct Postgres reads/writes for login accounts (the `users` table).

Ordinary application plumbing (signup/login), not LLM tool execution, so it
queries Postgres directly — same pattern as doctors_store.py.
"""
from __future__ import annotations

from typing import Any

from session_store import get_pool


async def get_user_by_email(email: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, name, email, password_hash, role, linked_id FROM users WHERE email = $1",
        email,
    )
    return dict(row) if row else None


async def create_user(name: str, email: str, password_hash: str, role: str, linked_id: int) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO users (name, email, password_hash, role, linked_id)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, name, email, password_hash, role, linked_id
        """,
        name,
        email,
        password_hash,
        role,
        linked_id,
    )
    return dict(row)
