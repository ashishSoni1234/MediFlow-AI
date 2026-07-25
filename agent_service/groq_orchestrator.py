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
import os
from typing import Any

from dotenv import load_dotenv
from groq import AsyncGroq

from mcp_client import mcp_session, tool_result_to_text, tool_to_groq_schema

load_dotenv()

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
            "book appointments. Always confirm doctor, date/time, and reason before "
            "booking."
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
            response = await groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=llm_tools,
                tool_choice="auto",
            )
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
