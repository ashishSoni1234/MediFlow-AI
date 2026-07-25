"""Agent Service — the MCP "client" in the client/server split.

Exposes a small HTTP API for the frontend. Internally, every user message
is handled by: load history -> run Groq tool-calling loop (which itself
performs real MCP tools/list + tools/call round trips) -> persist history.

Run standalone:
    python agent_service/main.py
"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import notifications_store
import session_store
from groq_orchestrator import build_system_message, run_agent_turn
from mcp_client import mcp_session

app = FastAPI(title="MediFlow-AI Agent Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AgentMessageRequest(BaseModel):
    session_id: str
    text: str
    role: str = "patient"  # "patient" or "doctor"
    user_ref: int | None = None


class AgentMessageResponse(BaseModel):
    session_id: str
    reply: str


@app.post("/agent/message", response_model=AgentMessageResponse)
async def agent_message(req: AgentMessageRequest) -> AgentMessageResponse:
    history = await session_store.load_history(req.session_id)

    if not history:
        history = [build_system_message(req.role)]

    history.append({"role": "user", "content": req.text})

    updated = await run_agent_turn(history)

    await session_store.save_history(req.session_id, updated, role=req.role, user_ref=req.user_ref)

    reply = next(
        (m["content"] for m in reversed(updated) if m["role"] == "assistant" and m.get("content")),
        "",
    )
    return AgentMessageResponse(session_id=req.session_id, reply=reply)


class SummaryRequest(BaseModel):
    session_id: str
    doctor_name: str
    date: str = "today"
    user_ref: int | None = None


@app.post("/agent/summary", response_model=AgentMessageResponse)
async def agent_summary(req: SummaryRequest) -> AgentMessageResponse:
    """Powers the doctor dashboard's "Get Today's Summary" button.

    Fetches the server-defined summarize_doctor_day PROMPT template (a
    genuine MCP prompts/get call, not a hardcoded f-string here) and runs
    the resulting text through the same tool-calling loop as a chat message.
    """
    history = await session_store.load_history(req.session_id)
    if not history:
        history = [build_system_message("doctor")]

    async with mcp_session() as session:
        prompt_result = await session.get_prompt(
            "summarize_doctor_day", {"doctor_name": req.doctor_name, "date": req.date}
        )

    for pm in prompt_result.messages:
        text = pm.content.text if hasattr(pm.content, "text") else str(pm.content)
        history.append({"role": pm.role, "content": text})

    updated = await run_agent_turn(history)
    await session_store.save_history(req.session_id, updated, role="doctor", user_ref=req.user_ref)

    reply = next(
        (m["content"] for m in reversed(updated) if m["role"] == "assistant" and m.get("content")),
        "",
    )
    return AgentMessageResponse(session_id=req.session_id, reply=reply)


@app.get("/doctor/schedule")
async def doctor_schedule(doctor_name: str, date: str = "today") -> dict:
    """Reads the doctor-schedule MCP RESOURCE directly (no LLM involved) —
    demonstrates resources as addressable reads, distinct from tool actions.
    """
    async with mcp_session() as session:
        result = await session.read_resource(f"doctor-schedule://{doctor_name}/{date}")
    text = result.contents[0].text if result.contents else "{}"
    return json.loads(text)


@app.get("/notifications/{doctor_id}")
async def get_notifications(doctor_id: int, unread_only: bool = False) -> list[dict]:
    return await notifications_store.list_notifications(doctor_id, unread_only)


@app.post("/notifications/{notification_id}/read")
async def read_notification(notification_id: int) -> dict:
    await notifications_store.mark_read(notification_id)
    return {"status": "ok"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8200, reload=False)
