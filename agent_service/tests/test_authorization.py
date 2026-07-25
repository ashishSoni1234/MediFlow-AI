"""Authorization regression tests for agent_service/main.py.

These cover three broken-object-level-authorization (IDOR) findings from a
code review, all of which have since been fixed in main.py:

1. GET /appointment/{id} had NO auth dependency at all — anyone could read
   any patient's name/email/reason for visit. Fixed by requiring
   get_current_user and checking the caller is the appointment's own doctor
   or patient.
2. POST /agent/summary never checked that `doctor_name` in the request body
   belonged to the authenticated doctor — one doctor could pull another
   doctor's patient data (and trigger a notification into their dashboard)
   just by naming them. Fixed by comparing the normalized doctor_name
   against the JWT's own claimed name.
3. POST /notifications/{id}/read never checked the notification's owning
   doctor before marking it read. Fixed by having notifications_store.mark_read
   filter on doctor_id atomically and 403 when it affects no row.

Run with: pytest agent_service/tests
"""
from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import notifications_store  # noqa: E402
import session_store  # noqa: E402
from auth import create_access_token, get_current_user, require_doctor  # noqa: E402

client = TestClient(main.app)

DOCTOR_A = {"user_id": 1, "role": "doctor", "linked_id": 1, "name": "Dr. Ahuja"}
DOCTOR_B_NAME = "Dr. Mehta"
PATIENT_A = {"user_id": 10, "role": "patient", "linked_id": 5, "name": "Jane Doe"}


def auth_header_for(claims: dict) -> dict:
    token = create_access_token(claims["user_id"], claims["role"], claims["linked_id"], claims["name"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    main.app.dependency_overrides.clear()


class FakePromptContent:
    def __init__(self, text):
        self.text = text


class FakePromptMessage:
    def __init__(self, role, text):
        self.role = role
        self.content = FakePromptContent(text)


class FakePromptResult:
    def __init__(self, text):
        self.messages = [FakePromptMessage("user", text)]


class FakeResourceContent:
    def __init__(self, text):
        self.text = text


class FakeResourceResult:
    def __init__(self, payload: dict):
        self.contents = [FakeResourceContent(json.dumps(payload))]


class FakeMcpSession:
    """Stands in for a real MCP ClientSession for read_resource/get_prompt/call_tool."""

    def __init__(self, resource_payload=None, prompt_text="summarize please"):
        self._resource_payload = resource_payload or {}
        self._prompt_text = prompt_text
        self.tool_calls: list[tuple[str, dict]] = []

    async def read_resource(self, uri):
        return FakeResourceResult(self._resource_payload)

    async def get_prompt(self, name, args):
        return FakePromptResult(self._prompt_text)

    async def call_tool(self, name, args):
        self.tool_calls.append((name, args))
        return SimpleNamespace(content=[], isError=False)


def patch_mcp_session(monkeypatch, session: FakeMcpSession):
    @asynccontextmanager
    async def fake_mcp_session():
        yield session

    monkeypatch.setattr(main, "mcp_session", fake_mcp_session)


# --- 1. Unauthenticated PHI disclosure via /appointment/{id} ----------------

def test_get_appointment_requires_authentication(monkeypatch):
    session = FakeMcpSession(
        resource_payload={
            "id": 1,
            "doctor_id": 1,
            "patient_id": 5,
            "doctor_name": "Dr. Ahuja",
            "patient_name": "Jane Doe",
            "patient_email": "jane@example.com",
            "reason": "fever",
        }
    )
    patch_mcp_session(monkeypatch, session)

    response = client.get("/appointment/1")  # deliberately no Authorization header

    assert response.status_code == 401


def test_get_appointment_rejects_non_owner(monkeypatch):
    """A different patient (not on this appointment) must not be able to read it."""
    session = FakeMcpSession(
        resource_payload={
            "id": 1,
            "doctor_id": 1,
            "patient_id": 5,
            "doctor_name": "Dr. Ahuja",
            "patient_name": "Jane Doe",
            "patient_email": "jane@example.com",
            "reason": "fever",
        }
    )
    patch_mcp_session(monkeypatch, session)
    main.app.dependency_overrides[get_current_user] = lambda: {
        "user_id": 99,
        "role": "patient",
        "linked_id": 999,  # not the patient_id (5) on this appointment
        "name": "Someone Else",
    }

    response = client.get("/appointment/1", headers=auth_header_for(PATIENT_A))

    assert response.status_code == 403


def test_get_appointment_allows_owning_patient(monkeypatch):
    session = FakeMcpSession(
        resource_payload={
            "id": 1,
            "doctor_id": 1,
            "patient_id": PATIENT_A["linked_id"],
            "doctor_name": "Dr. Ahuja",
            "patient_name": "Jane Doe",
            "patient_email": "jane@example.com",
            "reason": "fever",
        }
    )
    patch_mcp_session(monkeypatch, session)
    main.app.dependency_overrides[get_current_user] = lambda: PATIENT_A

    response = client.get("/appointment/1", headers=auth_header_for(PATIENT_A))

    assert response.status_code == 200
    body = response.json()
    assert body["patient_name"] == "Jane Doe"
    # Internal ids used only for the ownership check aren't leaked back out.
    assert "doctor_id" not in body
    assert "patient_id" not in body


def test_get_appointment_allows_owning_doctor(monkeypatch):
    session = FakeMcpSession(
        resource_payload={
            "id": 1,
            "doctor_id": DOCTOR_A["linked_id"],
            "patient_id": 5,
            "doctor_name": "Dr. Ahuja",
            "patient_name": "Jane Doe",
            "patient_email": "jane@example.com",
            "reason": "fever",
        }
    )
    patch_mcp_session(monkeypatch, session)
    main.app.dependency_overrides[get_current_user] = lambda: DOCTOR_A

    response = client.get("/appointment/1", headers=auth_header_for(DOCTOR_A))

    assert response.status_code == 200


# --- 2. Cross-doctor PHI disclosure via /agent/summary ----------------------

def test_agent_summary_rejects_requesting_another_doctors_report(monkeypatch):
    main.app.dependency_overrides[require_doctor] = lambda: DOCTOR_A

    async def fake_get_session_owner(session_id):
        return None

    async def fake_save_history(session_id, history, role=None, user_ref=None):
        return None

    async def fake_run_agent_turn(messages):
        messages.append({"role": "assistant", "content": "Dr. Mehta saw 5 patients today, 2 with fever."})
        return messages

    monkeypatch.setattr(session_store, "get_session_owner", fake_get_session_owner)
    monkeypatch.setattr(session_store, "save_history", fake_save_history)
    monkeypatch.setattr(main, "run_agent_turn", fake_run_agent_turn)
    patch_mcp_session(monkeypatch, FakeMcpSession())

    # Dr. Ahuja (DOCTOR_A) is authenticated, but asks for Dr. Mehta's report.
    response = client.post(
        "/agent/summary",
        json={"session_id": "s1", "doctor_name": DOCTOR_B_NAME, "date": "today"},
        headers=auth_header_for(DOCTOR_A),
    )

    assert response.status_code == 403


def test_agent_summary_allows_requesting_own_report(monkeypatch):
    main.app.dependency_overrides[require_doctor] = lambda: DOCTOR_A

    async def fake_get_session_owner(session_id):
        return None

    async def fake_load_history(session_id):
        return []

    async def fake_save_history(session_id, history, role=None, user_ref=None):
        return None

    async def fake_run_agent_turn(messages):
        messages.append({"role": "assistant", "content": "You saw 5 patients today."})
        return messages

    monkeypatch.setattr(session_store, "get_session_owner", fake_get_session_owner)
    monkeypatch.setattr(session_store, "load_history", fake_load_history)
    monkeypatch.setattr(session_store, "save_history", fake_save_history)
    monkeypatch.setattr(main, "run_agent_turn", fake_run_agent_turn)
    patch_mcp_session(monkeypatch, FakeMcpSession())

    # "dr. ahuja" (no title-case, trailing space) must still match "Dr. Ahuja".
    response = client.post(
        "/agent/summary",
        json={"session_id": "s1", "doctor_name": " dr ahuja ", "date": "today"},
        headers=auth_header_for(DOCTOR_A),
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "You saw 5 patients today."


# --- 2b. Same doctor_name-ownership gap on GET /doctor/schedule -------------

def test_doctor_schedule_rejects_viewing_another_doctors_schedule(monkeypatch):
    main.app.dependency_overrides[require_doctor] = lambda: DOCTOR_A
    patch_mcp_session(monkeypatch, FakeMcpSession(resource_payload={"doctor_name": DOCTOR_B_NAME, "appointments": []}))

    response = client.get(
        "/doctor/schedule", params={"doctor_name": DOCTOR_B_NAME}, headers=auth_header_for(DOCTOR_A)
    )

    assert response.status_code == 403


def test_doctor_schedule_allows_viewing_own_schedule(monkeypatch):
    main.app.dependency_overrides[require_doctor] = lambda: DOCTOR_A
    patch_mcp_session(
        monkeypatch, FakeMcpSession(resource_payload={"doctor_name": DOCTOR_A["name"], "appointments": []})
    )

    response = client.get(
        "/doctor/schedule", params={"doctor_name": DOCTOR_A["name"]}, headers=auth_header_for(DOCTOR_A)
    )

    assert response.status_code == 200


# --- 3. Cross-doctor notification mark-read via /notifications/{id}/read ---

def test_mark_notification_read_rejects_another_doctors_notification(monkeypatch):
    main.app.dependency_overrides[require_doctor] = lambda: DOCTOR_A

    calls = []

    async def fake_mark_read(notification_id, doctor_id):
        calls.append((notification_id, doctor_id))
        return False  # notification 999 belongs to some other doctor

    monkeypatch.setattr(notifications_store, "mark_read", fake_mark_read)

    response = client.post("/notifications/999/read", headers=auth_header_for(DOCTOR_A))

    assert response.status_code == 403
    # The store call itself is scoped to the caller's own doctor id — it's
    # the WHERE clause, not a client-supplied value, that enforces ownership.
    assert calls == [(999, DOCTOR_A["linked_id"])]


def test_mark_notification_read_allows_own_notification(monkeypatch):
    main.app.dependency_overrides[require_doctor] = lambda: DOCTOR_A

    async def fake_mark_read(notification_id, doctor_id):
        return True

    monkeypatch.setattr(notifications_store, "mark_read", fake_mark_read)

    response = client.post("/notifications/5/read", headers=auth_header_for(DOCTOR_A))

    assert response.status_code == 200


# --- sanity check: the ownership check that already existed still works ----

def test_get_notifications_rejects_viewing_another_doctors_notifications(monkeypatch):
    main.app.dependency_overrides[get_current_user] = lambda: DOCTOR_A

    other_doctor_id = DOCTOR_A["linked_id"] + 1
    response = client.get(f"/notifications/{other_doctor_id}", headers=auth_header_for(DOCTOR_A))

    assert response.status_code == 403
