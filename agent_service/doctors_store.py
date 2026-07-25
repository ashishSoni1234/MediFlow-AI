"""Direct Postgres reads for doctor identity.

Like session_store.py and notifications_store.py, this is ordinary
application plumbing rather than LLM tool execution, so it queries Postgres
directly instead of going through MCP — the "must go through MCP" rule only
applies to actions the LLM decides to take on a user's behalf. Login
credentials themselves live in the `users` table (see users_store.py);
this module is only used to validate that a doctor signup email is one of
the pre-seeded doctors, and to look up doctor identity by id.
"""
from __future__ import annotations

from typing import Any

from session_store import get_pool


async def get_doctor_by_email(email: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT id, name, email FROM doctors WHERE email = $1", email)
    return dict(row) if row else None
