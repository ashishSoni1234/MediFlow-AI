"""Direct Postgres reads for the doctor dashboard's notifications panel.

Unlike tool execution (which must go through MCP), reading notifications
to render a UI list is ordinary application plumbing, not an AI-orchestrated
action, so it queries Postgres directly like session_store.py does.
"""
from __future__ import annotations

from typing import Any

from session_store import get_pool


async def list_notifications(doctor_id: int, unread_only: bool = False) -> list[dict[str, Any]]:
    pool = await get_pool()
    query = "SELECT id, doctor_id, message, channel, created_at, read FROM notifications WHERE doctor_id = $1"
    if unread_only:
        query += " AND read = false"
    query += " ORDER BY created_at DESC LIMIT 50"
    rows = await pool.fetch(query, doctor_id)
    return [
        {
            "id": r["id"],
            "doctor_id": r["doctor_id"],
            "message": r["message"],
            "channel": r["channel"],
            "created_at": r["created_at"].isoformat(),
            "read": r["read"],
        }
        for r in rows
    ]


async def mark_read(notification_id: int) -> None:
    pool = await get_pool()
    await pool.execute("UPDATE notifications SET read = true WHERE id = $1", notification_id)
