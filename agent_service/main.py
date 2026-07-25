"""Agent Service — the MCP "client" in the client/server split.

Exposes a small HTTP API for the frontend. Internally, every user message
is handled by: load history -> run Groq tool-calling loop (which itself
performs real MCP tools/list + tools/call round trips) -> persist history.

Run standalone:
    python agent_service/main.py
"""
from __future__ import annotations

import json
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import doctors_store
import notifications_store
import patients_store
import session_store
import users_store
from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_doctor,
    verify_password,
    verify_supabase_token,
)
from groq_orchestrator import build_system_message, run_agent_turn
from mcp_client import mcp_session

app = FastAPI(title="MediFlow-AI Agent Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AuthResponse(BaseModel):
    token: str
    role: str
    name: str
    linked_id: int


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str
    role: str  # "patient" or "doctor"


@app.post("/auth/signup", response_model=AuthResponse)
async def signup(req: SignupRequest) -> AuthResponse:
    """Patients can sign up freely. Doctor signup is restricted to the
    pre-seeded doctor emails in db/seed.sql — this stops a random signup
    from minting a new, unverified doctor identity.
    """
    email = req.email.strip().lower()
    if req.role not in ("patient", "doctor"):
        raise HTTPException(status_code=400, detail="role must be 'patient' or 'doctor'")
    if await users_store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    if req.role == "doctor":
        doctor = await doctors_store.get_doctor_by_email(email)
        if not doctor:
            raise HTTPException(status_code=403, detail="This email is not a pre-approved doctor account")
        linked_id, name = doctor["id"], doctor["name"]
    else:
        patient = await patients_store.get_patient_by_email(email)
        if not patient:
            patient = await patients_store.create_patient(req.name, email)
        linked_id, name = patient["id"], req.name

    user = await users_store.create_user(name, email, hash_password(req.password), req.role, linked_id)
    token = create_access_token(user["id"], user["role"], user["linked_id"], user["name"])
    return AuthResponse(token=token, role=user["role"], name=user["name"], linked_id=user["linked_id"])


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/login", response_model=AuthResponse)
async def login(req: LoginRequest) -> AuthResponse:
    user = await users_store.get_user_by_email(req.email.strip().lower())
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], user["role"], user["linked_id"], user["name"])
    return AuthResponse(token=token, role=user["role"], name=user["name"], linked_id=user["linked_id"])


class GoogleAuthRequest(BaseModel):
    access_token: str


@app.post("/auth/google", response_model=AuthResponse)
async def auth_google(req: GoogleAuthRequest) -> AuthResponse:
    """Signs in (or signs up) via a Google identity that Supabase Auth has
    already verified. One endpoint covers both cases, same as the "Sign in
    with Google" button being shared by the login/signup panel: an existing
    email logs in as-is, a new email is provisioned the same way manual
    signup provisions one (doctor only if pre-approved, patient otherwise).
    """
    supabase_user = await verify_supabase_token(req.access_token)
    email = (supabase_user.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email")
    google_name = (supabase_user.get("user_metadata") or {}).get("full_name") or email

    user = await users_store.get_user_by_email(email)
    if not user:
        doctor = await doctors_store.get_doctor_by_email(email)
        if doctor:
            role, linked_id, name = "doctor", doctor["id"], doctor["name"]
        else:
            patient = await patients_store.get_patient_by_email(email)
            if not patient:
                patient = await patients_store.create_patient(google_name, email)
            role, linked_id, name = "patient", patient["id"], patient["name"]
        placeholder_hash = hash_password(secrets.token_urlsafe(32))
        user = await users_store.create_user(name, email, placeholder_hash, role, linked_id)

    token = create_access_token(user["id"], user["role"], user["linked_id"], user["name"])
    return AuthResponse(token=token, role=user["role"], name=user["name"], linked_id=user["linked_id"])


class AgentMessageRequest(BaseModel):
    session_id: str
    text: str


class AgentMessageResponse(BaseModel):
    session_id: str
    reply: str


@app.post("/agent/message", response_model=AgentMessageResponse)
async def agent_message(
    req: AgentMessageRequest, user: dict = Depends(get_current_user)
) -> AgentMessageResponse:
    role, user_ref = user["role"], user["linked_id"]

    owner = await session_store.get_session_owner(req.session_id)
    if owner is not None and owner != (role, user_ref):
        raise HTTPException(status_code=403, detail="This session belongs to a different account")

    await session_store.log_prompt(role, user_ref, req.text)

    history = await session_store.load_history(req.session_id)
    if not history:
        history = [build_system_message(role)]

    history.append({"role": "user", "content": req.text})

    updated = await run_agent_turn(history)

    await session_store.save_history(req.session_id, updated, role=role, user_ref=user_ref)

    reply = next(
        (m["content"] for m in reversed(updated) if m["role"] == "assistant" and m.get("content")),
        "",
    )
    return AgentMessageResponse(session_id=req.session_id, reply=reply)


class SummaryRequest(BaseModel):
    session_id: str
    doctor_name: str
    date: str = "today"


@app.post("/agent/summary", response_model=AgentMessageResponse)
async def agent_summary(
    req: SummaryRequest, user: dict = Depends(require_doctor)
) -> AgentMessageResponse:
    """Powers the doctor dashboard's "Get Today's Summary" button.

    Fetches the server-defined summarize_doctor_day PROMPT template (a
    genuine MCP prompts/get call, not a hardcoded f-string here) and runs
    the resulting text through the same tool-calling loop as a chat message.
    """
    owner = await session_store.get_session_owner(req.session_id)
    if owner is not None and owner != ("doctor", user["linked_id"]):
        raise HTTPException(status_code=403, detail="This session belongs to a different account")

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
    await session_store.save_history(req.session_id, updated, role="doctor", user_ref=user["linked_id"])

    reply = next(
        (m["content"] for m in reversed(updated) if m["role"] == "assistant" and m.get("content")),
        "",
    )

    # Scenario 2 asks for the report to arrive via a channel distinct from
    # Scenario 1's email — deliver it deterministically (in-app, the
    # dashboard's notifications panel) rather than leaving that decision to
    # the LLM, the same reasoning as auto-sending the booking confirmation.
    if reply:
        async with mcp_session() as session:
            await session.call_tool(
                "notify_doctor",
                {"doctor_name": req.doctor_name, "message": reply, "channel": "in_app"},
            )

    return AgentMessageResponse(session_id=req.session_id, reply=reply)


@app.get("/doctor/schedule")
async def doctor_schedule(doctor_name: str, date: str = "today", user: dict = Depends(require_doctor)) -> dict:
    """Reads the doctor-schedule MCP RESOURCE directly (no LLM involved) —
    demonstrates resources as addressable reads, distinct from tool actions.
    """
    async with mcp_session() as session:
        result = await session.read_resource(f"doctor-schedule://{doctor_name}/{date}")
    text = result.contents[0].text if result.contents else "{}"
    return json.loads(text)


@app.get("/appointment/{appointment_id}")
async def get_appointment(appointment_id: int) -> dict:
    """Reads the appointment MCP RESOURCE directly (no LLM involved) — the
    per-appointment counterpart to /doctor/schedule above.
    """
    async with mcp_session() as session:
        result = await session.read_resource(f"appointment://{appointment_id}")
    text = result.contents[0].text if result.contents else "{}"
    return json.loads(text)


@app.get("/notifications/{doctor_id}")
async def get_notifications(
    doctor_id: int, unread_only: bool = False, user: dict = Depends(require_doctor)
) -> list[dict]:
    if doctor_id != user["linked_id"]:
        raise HTTPException(status_code=403, detail="Cannot view another doctor's notifications")
    return await notifications_store.list_notifications(doctor_id, unread_only)


@app.post("/notifications/{notification_id}/read")
async def read_notification(notification_id: int, user: dict = Depends(require_doctor)) -> dict:
    await notifications_store.mark_read(notification_id)
    return {"status": "ok"}


@app.get("/sessions")
async def list_sessions(user: dict = Depends(get_current_user)) -> list[dict]:
    """Sidebar data: this user's past chat sessions, most recently active first."""
    return await session_store.list_sessions(user["role"], user["linked_id"])


@app.get("/session/{session_id}/history")
async def get_session_history(session_id: str, user: dict = Depends(get_current_user)) -> list[dict]:
    """Lets the frontend rehydrate the visible transcript for a persisted
    session_id (e.g. after a page reload or sidebar switch) — filters out
    the system prompt and tool-call/tool-result plumbing, leaving just what
    a user actually typed or read.
    """
    owner = await session_store.get_session_owner(session_id)
    if owner is not None and owner != (user["role"], user["linked_id"]):
        raise HTTPException(status_code=403, detail="This session belongs to a different account")

    history = await session_store.load_history(session_id)
    return [
        {"role": m["role"], "content": m["content"]}
        for m in history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]


class RenameSessionRequest(BaseModel):
    title: str


@app.patch("/session/{session_id}")
async def rename_session(
    session_id: str, req: RenameSessionRequest, user: dict = Depends(get_current_user)
) -> dict:
    """Renames a session's sidebar/history title (chat content is untouched)."""
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    ok = await session_store.rename_session(session_id, title, user["role"], user["linked_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@app.delete("/session/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    ok = await session_store.delete_session(session_id, user["role"], user["linked_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}


@app.delete("/sessions")
async def delete_all_sessions(user: dict = Depends(get_current_user)) -> dict:
    """Clears every chat session for the signed-in user (sidebar + history)."""
    deleted = await session_store.delete_all_sessions(user["role"], user["linked_id"])
    return {"deleted": deleted}


@app.get("/prompt-history")
async def prompt_history(user: dict = Depends(get_current_user)) -> list[dict]:
    """Read-only, flat log of every prompt this user has ever typed, newest first."""
    return await session_store.list_prompts(user["role"], user["linked_id"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8200, reload=False)
