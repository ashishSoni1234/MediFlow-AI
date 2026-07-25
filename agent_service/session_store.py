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


def _make_title(history: list[dict[str, Any]]) -> str | None:
    """First ~6 words of the first user message, used as a session's
    display title in the sidebar. Only takes effect on first insert (see
    the COALESCE in save_history) — a session's title never changes after.
    """
    first_user = next((m for m in history if m.get("role") == "user" and m.get("content")), None)
    if not first_user:
        return None
    words = str(first_user["content"]).split()
    title = " ".join(words[:6])
    return title + ("…" if len(words) > 6 else "")


async def save_history(
    session_id: str, history: list[dict[str, Any]], role: str | None = None, user_ref: int | None = None
) -> None:
    pool = await get_pool()
    title = _make_title(history)
    await pool.execute(
        """
        INSERT INTO conversation_sessions (session_id, role, user_ref, title, history, updated_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, now())
        ON CONFLICT (session_id)
        DO UPDATE SET history = $5::jsonb, updated_at = now(),
                       role = COALESCE(EXCLUDED.role, conversation_sessions.role),
                       user_ref = COALESCE(EXCLUDED.user_ref, conversation_sessions.user_ref),
                       title = COALESCE(conversation_sessions.title, EXCLUDED.title)
        """,
        session_id,
        role,
        user_ref,
        title,
        json.dumps(history),
    )


async def list_sessions(role: str, user_ref: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT session_id, title, updated_at FROM conversation_sessions
        WHERE role = $1 AND user_ref = $2
        ORDER BY updated_at DESC
        """,
        role,
        user_ref,
    )
    return [
        {"session_id": r["session_id"], "title": r["title"], "updated_at": r["updated_at"].isoformat()}
        for r in rows
    ]


async def rename_session(session_id: str, title: str, role: str, user_ref: int) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "UPDATE conversation_sessions SET title = $1 WHERE session_id = $2 AND role = $3 AND user_ref = $4",
        title,
        session_id,
        role,
        user_ref,
    )
    return result.endswith(" 1")


async def delete_session(session_id: str, role: str, user_ref: int) -> bool:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM conversation_sessions WHERE session_id = $1 AND role = $2 AND user_ref = $3",
        session_id,
        role,
        user_ref,
    )
    return result.endswith(" 1")


async def delete_all_sessions(role: str, user_ref: int) -> int:
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM conversation_sessions WHERE role = $1 AND user_ref = $2",
        role,
        user_ref,
    )
    return int(result.rsplit(" ", 1)[-1])


async def get_session_owner(session_id: str) -> tuple[str, int] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT role, user_ref FROM conversation_sessions WHERE session_id = $1", session_id
    )
    if not row or row["role"] is None or row["user_ref"] is None:
        return None
    return row["role"], row["user_ref"]


async def log_prompt(role: str, user_ref: int, text: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO prompt_log (role, user_ref, text) VALUES ($1, $2, $3)",
        role,
        user_ref,
        text,
    )


async def list_prompts(role: str, user_ref: int) -> list[dict[str, Any]]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT text, created_at FROM prompt_log
        WHERE role = $1 AND user_ref = $2
        ORDER BY created_at DESC
        """,
        role,
        user_ref,
    )
    return [{"text": r["text"], "timestamp": r["created_at"].isoformat()} for r in rows]
