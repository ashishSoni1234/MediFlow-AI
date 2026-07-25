"""Direct Postgres reads/writes for patient identity at signup time.

agent_service can't reach into mcp_server's db.find_or_create_patient (a
different process reachable only over MCP), so patient signup needs its own
small direct-Postgres path here — same pattern as doctors_store.py.
"""
from __future__ import annotations

from typing import Any

from session_store import get_pool


async def get_patient_by_email(email: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT id, name, email, phone FROM patients WHERE email = $1", email)
    return dict(row) if row else None


async def create_patient(name: str, email: str, phone: str | None = None) -> dict[str, Any]:
    pool = await get_pool()
    row = await pool.fetchrow(
        "INSERT INTO patients (name, email, phone) VALUES ($1, $2, $3) RETURNING id, name, email, phone",
        name,
        email,
        phone,
    )
    return dict(row)
