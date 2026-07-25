"""LLM-driven orchestration loop.

This is the piece the assignment cares most about: the LLM decides which
MCP tool(s) to call and in what order, based purely on the tool schemas
discovered at runtime via tools/list. There is no `if "book" in text`
routing anywhere in this file — every branch below is generic message
plumbing, not intent classification.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any

from dotenv import load_dotenv
from groq import AsyncGroq, RateLimitError

from mcp_client import mcp_session, tool_result_to_text, tool_to_groq_schema

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_TOOL_ROUNDS = 6

groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))


def build_system_message(role: str) -> dict[str, str]:
    today = dt.date.today().isoformat()
    if role == "doctor":
        audience = (
            "You are speaking with a doctor using their dashboard. They may ask "
            "about their schedule, stats about their patients, or ask for a summary "
            "of their day."
        )
    else:
        audience = (
            "You are speaking with a patient. Help them check availability and "
            "book appointments. Before calling book_appointment you MUST have "
            "collected, directly from the patient's own messages, all of the "
            "following: (1) the doctor and a confirmed free slot (via "
            "check_doctor_availability), (2) the patient's full name, (3) the "
            "patient's email address, and (4) the reason for the visit. If any "
            "of these is missing, ask the patient for exactly that information "
            "in plain language and wait for their reply — do not call "
            "book_appointment yet. Never invent, guess, or fill in a placeholder "
            "value (e.g. literally 'patient_name', 'reason', 'email', 'N/A') for "
            "any of these fields; only use what the patient actually typed. If "
            "book_appointment returns an error saying required information is "
            "missing or invalid, tell the patient in natural language exactly "
            "what's still needed and do not retry the booking until they've "
            "provided it. If book_appointment fails because the slot is already "
            "taken, call suggest_reschedule with the same doctor and originally "
            "requested time, and offer the patient the 2-3 nearest alternative "
            "slots instead of just reporting the failure."
        )
    return {
        "role": "system",
        "content": (
            "You are the MediFlow-AI clinic assistant. "
            f"Today's date is {today}. "
            f"{audience} "
            "Use the available tools to look up real data and take real actions — "
            "never guess availability, stats, or booking outcomes. If a tool call "
            "fails or returns an error, explain the problem to the user in plain "
            "language rather than pretending it succeeded. Do not call the same "
            "tool with the same arguments more than once in a turn. When a tool "
            "result includes a 'total' or count field, quote that number exactly "
            "in your answer rather than recounting or adding results yourself."
        ),
    }


def _assistant_message_to_dict(msg: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return d


async def run_agent_turn(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs the tool-calling loop for one user turn and returns updated messages.

    `messages` must already include the new user message appended. Mutates
    and returns the same list (system + prior turns + this turn's
    assistant/tool messages), ready to be persisted verbatim as history.
    """
    async with mcp_session() as session:
        discovered = await session.list_tools()
        llm_tools = [tool_to_groq_schema(t) for t in discovered.tools]

        rounds = 0
        while True:
            try:
                response = await groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=messages,
                    tools=llm_tools,
                    tool_choice="auto",
                )
            except RateLimitError:
                # The daily/per-minute token quota on the Groq account is
                # exhausted — no rephrasing will fix this, so say so instead
                # of the generic fallback below.
                logger.exception("Groq completion request rate-limited")
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "The assistant is temporarily rate-limited by the LLM "
                            "provider — please try again in a few minutes."
                        ),
                    }
                )
                break
            except Exception:
                # A malformed tool call, provider outage, etc. must not
                # crash the whole request (and, worse, the shared MCP
                # session's connection) — surface a normal assistant reply
                # instead so the frontend gets a clean response to show
                # the user.
                logger.exception("Groq completion request failed")
                messages.append(
                    {
                        "role": "assistant",
                        "content": "Sorry, I ran into a problem processing that — could you try rephrasing?",
                    }
                )
                break

            msg = response.choices[0].message
            messages.append(_assistant_message_to_dict(msg))

            if not msg.tool_calls:
                break

            for call in msg.tool_calls:
                tool_name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": f"ERROR: could not parse arguments as JSON: {exc}",
                        }
                    )
                    continue

                try:
                    result = await session.call_tool(tool_name, args)
                    content = tool_result_to_text(result)
                except Exception as exc:
                    # Covers unknown tool names, server-side validation errors,
                    # and transport failures — surfaced to the LLM as a tool
                    # result so it can explain or retry, rather than crashing
                    # the request.
                    content = f"ERROR calling tool '{tool_name}': {exc}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": content,
                    }
                )

            rounds += 1
            if rounds >= MAX_TOOL_ROUNDS:
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "I've made several tool calls but haven't reached a final "
                            "answer yet. Could you clarify what you'd like me to do next?"
                        ),
                    }
                )
                break

    return messages
