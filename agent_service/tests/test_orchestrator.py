"""Tests for the tool-calling loop in groq_orchestrator.py — the piece the
assignment cares about most (LLM-driven MCP orchestration) and the one part
of the codebase that previously had zero test coverage.

Groq and the MCP server are both faked here (no live Groq API key or MCP
server process needed): `groq_client.chat.completions.create` is monkeypatched
directly, and `mcp_session` is monkeypatched to yield an in-memory fake
session, so these run anywhere.

Run with: pytest agent_service/tests
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402
from groq import RateLimitError  # noqa: E402

import groq_orchestrator as orch  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# --- fakes ---------------------------------------------------------------

class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeResponse:
    def __init__(self, message):
        self.choices = [FakeChoice(message)]


class FakeTool:
    def __init__(self, name, description="", input_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class FakeToolsList:
    def __init__(self, tools):
        self.tools = tools


class FakeContentBlock:
    def __init__(self, text):
        self.text = text


class FakeCallToolResult:
    def __init__(self, text, is_error=False):
        self.content = [FakeContentBlock(text)]
        self.isError = is_error


class FakeSession:
    """Records every call_tool invocation so tests can assert on it."""

    def __init__(self, tools, result_text="ok", is_error=False):
        self._tools = tools
        self._result_text = result_text
        self._is_error = is_error
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return FakeToolsList(self._tools)

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return FakeCallToolResult(self._result_text, is_error=self._is_error)


def patch_mcp_session(monkeypatch, session: FakeSession):
    @asynccontextmanager
    async def fake_mcp_session():
        yield session

    monkeypatch.setattr(orch, "mcp_session", fake_mcp_session)


def patch_groq_responses(monkeypatch, responses: list[FakeResponse]):
    """Each call to chat.completions.create pops the next canned response."""
    remaining = list(responses)

    async def fake_create(**kwargs):
        if not remaining:
            raise AssertionError("Groq was called more times than the test expected")
        return remaining.pop(0)

    monkeypatch.setattr(orch.groq_client.chat.completions, "create", fake_create)


# --- build_system_message -------------------------------------------------

def test_system_message_for_patient_forbids_placeholder_booking():
    msg = orch.build_system_message("patient")
    assert msg["role"] == "system"
    assert "book_appointment" in msg["content"]
    assert "Never invent, guess, or fill in a placeholder" in msg["content"]


def test_system_message_for_doctor_mentions_dashboard():
    msg = orch.build_system_message("doctor")
    assert "dashboard" in msg["content"]


# --- run_agent_turn: no tool call needed ----------------------------------

def test_run_agent_turn_direct_answer_without_tool_calls(monkeypatch):
    session = FakeSession(tools=[FakeTool("check_doctor_availability")])
    patch_mcp_session(monkeypatch, session)
    patch_groq_responses(
        monkeypatch,
        [FakeResponse(FakeMessage(content="Hi! How can I help?"))],
    )

    messages = [orch.build_system_message("patient"), {"role": "user", "content": "hello"}]
    result = run(orch.run_agent_turn(messages))

    assert result[-1] == {"role": "assistant", "content": "Hi! How can I help?"}
    assert session.calls == []  # no tool should have been invoked


# --- run_agent_turn: single tool round trip -------------------------------

def test_run_agent_turn_executes_tool_call_and_feeds_result_back(monkeypatch):
    session = FakeSession(
        tools=[FakeTool("check_doctor_availability")],
        result_text='{"free_slots": ["2026-07-27T10:00:00"]}',
    )
    patch_mcp_session(monkeypatch, session)
    patch_groq_responses(
        monkeypatch,
        [
            FakeResponse(
                FakeMessage(
                    content=None,
                    tool_calls=[
                        FakeToolCall(
                            "call_1",
                            "check_doctor_availability",
                            '{"doctor_name": "Ahuja", "date": "2026-07-27"}',
                        )
                    ],
                )
            ),
            FakeResponse(FakeMessage(content="Dr. Ahuja is free at 10am tomorrow.")),
        ],
    )

    messages = [orch.build_system_message("patient"), {"role": "user", "content": "is Ahuja free tomorrow?"}]
    result = run(orch.run_agent_turn(messages))

    assert session.calls == [("check_doctor_availability", {"doctor_name": "Ahuja", "date": "2026-07-27"})]
    tool_messages = [m for m in result if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert "free_slots" in tool_messages[0]["content"]
    assert result[-1]["content"] == "Dr. Ahuja is free at 10am tomorrow."


def test_run_agent_turn_marks_tool_error_results_for_the_llm(monkeypatch):
    session = FakeSession(
        tools=[FakeTool("book_appointment")],
        result_text="Ahuja is already booked at that time.",
        is_error=True,
    )
    patch_mcp_session(monkeypatch, session)
    patch_groq_responses(
        monkeypatch,
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[FakeToolCall("call_1", "book_appointment", "{}")],
                )
            ),
            FakeResponse(FakeMessage(content="That slot is taken, want another time?")),
        ],
    )

    messages = [orch.build_system_message("patient"), {"role": "user", "content": "book it"}]
    result = run(orch.run_agent_turn(messages))

    tool_messages = [m for m in result if m["role"] == "tool"]
    assert tool_messages[0]["content"].startswith("ERROR:")


def test_run_agent_turn_handles_malformed_tool_call_arguments(monkeypatch):
    session = FakeSession(tools=[FakeTool("book_appointment")])
    patch_mcp_session(monkeypatch, session)
    patch_groq_responses(
        monkeypatch,
        [
            FakeResponse(
                FakeMessage(tool_calls=[FakeToolCall("call_1", "book_appointment", "{not valid json")]),
            ),
            FakeResponse(FakeMessage(content="Sorry, could you rephrase that?")),
        ],
    )

    messages = [orch.build_system_message("patient"), {"role": "user", "content": "book it"}]
    result = run(orch.run_agent_turn(messages))

    assert session.calls == []  # tool must never be invoked with unparsable args
    tool_messages = [m for m in result if m["role"] == "tool"]
    assert "could not parse arguments" in tool_messages[0]["content"]


# --- run_agent_turn: loop guards -------------------------------------------

def test_run_agent_turn_stops_after_max_tool_rounds(monkeypatch):
    session = FakeSession(tools=[FakeTool("check_doctor_availability")], result_text="{}")
    patch_mcp_session(monkeypatch, session)

    # The model never stops asking for tool calls -> loop must bail out
    # after MAX_TOOL_ROUNDS instead of looping forever.
    infinite_tool_call_response = FakeResponse(
        FakeMessage(tool_calls=[FakeToolCall("call_x", "check_doctor_availability", "{}")])
    )
    patch_groq_responses(monkeypatch, [infinite_tool_call_response] * orch.MAX_TOOL_ROUNDS)

    messages = [orch.build_system_message("patient"), {"role": "user", "content": "keep going"}]
    result = run(orch.run_agent_turn(messages))

    assert len(session.calls) == orch.MAX_TOOL_ROUNDS
    assert "haven't reached a final answer" in result[-1]["content"]


def test_run_agent_turn_recovers_pseudo_tool_call_leaked_as_text(monkeypatch):
    """Reproduces a real failure: llama-3.1-8b-instant on Groq sometimes
    emits a tool call as a literal `<function=name>{...}</function>` tag in
    plain-text content instead of a structured tool_calls entry, and Groq
    returns this as an ordinary 200 (not the tool_use_failed 400 that
    _complete_with_retry already handles). Without recovery, the raw tag
    text leaks straight to the user as if it were the final answer.
    """
    session = FakeSession(
        tools=[FakeTool("get_appointment_stats")],
        result_text='{"total": 2, "appointments": []}',
    )
    patch_mcp_session(monkeypatch, session)
    patch_groq_responses(
        monkeypatch,
        [
            FakeResponse(
                FakeMessage(
                    content=(
                        "Let me check that.\n"
                        '<function=get_appointment_stats>{"date_range": '
                        '"2026-07-26:2026-07-27", "doctor_name": "Dr. Ahuja", '
                        '"filter_reason": "fever"}</function>'
                    ),
                    tool_calls=[],
                )
            ),
            FakeResponse(FakeMessage(content="2 patients came in with fever.")),
        ],
    )

    messages = [
        orch.build_system_message("doctor"),
        {"role": "user", "content": "how many patients came in with fever?"},
    ]
    result = run(orch.run_agent_turn(messages))

    assert session.calls == [
        (
            "get_appointment_stats",
            {
                "date_range": "2026-07-26:2026-07-27",
                "doctor_name": "Dr. Ahuja",
                "filter_reason": "fever",
            },
        )
    ]
    # the raw <function=...> tag must never reach the user
    for m in result:
        if m["content"]:
            assert "<function=" not in m["content"]
    assert result[-1]["content"] == "2 patients came in with fever."


def test_run_agent_turn_recovers_malformed_pseudo_tool_call_variants(monkeypatch):
    """Reproduces a real production leak: llama-3.1-8b-instant emitted
    `=function=name>{...}` (no leading `<`, no closing `</function>` tag) and
    `(function=name>{...}` in the same reply, interleaved with narrated
    reasoning ("I need to get today's date..."). The original recovery regex
    only matched the well-formed `<function=name>{...}</function>` tag, so
    none of this was recognized as a tool call and the whole thing —
    reasoning text included — leaked to the user verbatim.
    """
    session = FakeSession(
        tools=[FakeTool("check_doctor_availability"), FakeTool("book_appointment")],
        result_text='{"slots": ["2026-07-27T10:00:00"]}',
    )
    patch_mcp_session(monkeypatch, session)
    patch_groq_responses(
        monkeypatch,
        [
            FakeResponse(
                FakeMessage(
                    content=(
                        "I need to get today's date to check the correct "
                        "availability on Wednesday.\n\n"
                        "Today's date is July 26, which means Wednesday's "
                        "date would be July 27.\n\n"
                        '=function=check_doctor_availability>{"date": '
                        '"2026-07-27", "doctor_name": "Dr. Ahuja", '
                        '"time_window": null}\n\n'
                        "Please wait for the result.\n\n"
                        '(function=book_appointment>{"doctor_name": "Dr. Ahuja", '
                        '"patient_name": "Pankaj", "patient_email": '
                        '"soniashish37066@gmail.com", "datetime_iso": "", '
                        '"reason": "fever and cold", "duration_minutes": 30}'
                    ),
                    tool_calls=[],
                )
            ),
            FakeResponse(FakeMessage(content="Dr. Ahuja is free at 10:00 AM on Wednesday.")),
        ],
    )

    messages = [
        orch.build_system_message("patient"),
        {"role": "user", "content": "check wednesday availability and book it"},
    ]
    result = run(orch.run_agent_turn(messages))

    assert session.calls == [
        (
            "check_doctor_availability",
            {"date": "2026-07-27", "doctor_name": "Dr. Ahuja", "time_window": None},
        ),
        (
            "book_appointment",
            {
                "doctor_name": "Dr. Ahuja",
                "patient_name": "Pankaj",
                "patient_email": "soniashish37066@gmail.com",
                "datetime_iso": "",
                "reason": "fever and cold",
                "duration_minutes": 30,
            },
        ),
    ]
    # neither the raw tag syntax nor the narrated reasoning may reach the user
    for m in result:
        if m["role"] == "assistant" and m["content"]:
            assert "function=" not in m["content"]
            assert "I need to get today's date" not in m["content"]
    assert result[-1]["content"] == "Dr. Ahuja is free at 10:00 AM on Wednesday."


def test_run_agent_turn_surfaces_rate_limit_error_without_crashing(monkeypatch):
    session = FakeSession(tools=[FakeTool("check_doctor_availability")])
    patch_mcp_session(monkeypatch, session)

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request)
    error = RateLimitError("rate limited", response=response, body=None)

    async def raising_create(**kwargs):
        raise error

    monkeypatch.setattr(orch.groq_client.chat.completions, "create", raising_create)

    messages = [orch.build_system_message("patient"), {"role": "user", "content": "hi"}]
    result = run(orch.run_agent_turn(messages))

    assert "rate-limited" in result[-1]["content"]
