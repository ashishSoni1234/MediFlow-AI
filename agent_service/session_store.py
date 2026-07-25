"""Conversation history persistence, keyed by session_id.

Backed by the conversation_sessions table (Postgres). This is what makes
multi-turn follow-ups like "book the 3 PM slot" work: the full message
array (including prior tool calls/results) is reloaded and handed back
to the LLM on every turn, instead of re-deriving intent with if/else logic.
"""
from __future__ import annotations

import json
import os
from typing import Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://mediflow:mediflow@localhost:5433/mediflow"
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


async def load_history(session_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT history FROM conversation_sessions WHERE session_id = $1", session_id
    )
    if not row:
        return []
    history = row["history"]
    return json.loads(history) if isinstance(history, str) else history


async def save_history(
    session_id: str, history: list[dict[str, Any]], role: str | None = None, user_ref: int | None = None
) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO conversation_sessions (session_id, role, user_ref, history, updated_at)
        VALUES ($1, $2, $3, $4::jsonb, now())
        ON CONFLICT (session_id)
        DO UPDATE SET history = $4::jsonb, updated_at = now(),
                       role = COALESCE(EXCLUDED.role, conversation_sessions.role),
                       user_ref = COALESCE(EXCLUDED.user_ref, conversation_sessions.user_ref)
        """,
        session_id,
        role,
        user_ref,
        json.dumps(history),
    )
