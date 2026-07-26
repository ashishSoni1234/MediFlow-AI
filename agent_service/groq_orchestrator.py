"""LLM-driven orchestration loop.

This is the piece the assignment cares most about: the LLM decides which
MCP tool(s) to call and in what order, based purely on the tool schemas
discovered at runtime via tools/list. There is no `if "book" in text`
routing anywhere in this file — every branch below is generic message
plumbing, not intent classification.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import uuid
from typing import Any

from dotenv import load_dotenv
from groq import AsyncGroq, BadRequestError, RateLimitError

from mcp_client import mcp_session, tool_result_to_text, tool_to_groq_schema

load_dotenv()

logger = logging.getLogger(__name__)

# Llama on Groq occasionally emits a tool call as literal text inside the
# plain-text content instead of a structured tool_calls entry. The tag is
# supposed to look like `<function=name>{...}</function>`, but in practice
# the leading punctuation varies (`<function=`, `=function=`, `(function=`,
# or nothing at all) and the closing `</function>` tag is frequently missing
# entirely. Sometimes Groq itself rejects the well-formed case as a
# `tool_use_failed` 400 (handled in _complete_with_retry below), but other
# times it comes back as an ordinary 200 with empty tool_calls — which, left
# alone, makes the loop treat the raw tag text (and any reasoning narrated
# around it) as the final answer and leak it straight to the user.
_PSEUDO_FUNCTION_CALL_HEADER_RE = re.compile(r"[<(=]?function=(?P<name>[a-zA-Z0-9_]+)>")


def _extract_pseudo_tool_calls(content: str | None) -> list[dict[str, Any]] | None:
    """Recovers tool calls Llama emitted as literal text so they can be
    executed like normal tool calls, instead of showing the raw tag (and any
    surrounding reasoning text) to the user as if it were the assistant's
    answer.

    Only the opening `name>` header is matched strictly; the JSON argument
    object that follows is parsed with a real JSON decoder (scanning for the
    matching closing brace) rather than a `{.*?}` regex, since that assumed
    a closing `</function>` tag terminates it — which real leaks often omit.
    """
    if not content:
        return None
    decoder = json.JSONDecoder()
    calls: list[dict[str, Any]] = []
    for m in _PSEUDO_FUNCTION_CALL_HEADER_RE.finditer(content):
        tail = content[m.end():]
        if not tail.startswith("{"):
            continue  # header must be immediately followed by the JSON object
        try:
            args_obj, _end = decoder.raw_decode(tail)
        except json.JSONDecodeError:
            continue
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": m.group("name").strip(),
                    "arguments": json.dumps(args_obj),
                },
            }
        )
    return calls or None

# llama-3.1-8b-instant instead of the 70b model: same tool-calling loop needs
# several completions per turn (see MAX_TOOL_ROUNDS below), and the 8b model
# gets a much higher free-tier RPM/TPM allowance on Groq, so it clears the
# rate limit far less often for this workload.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
MAX_TOOL_ROUNDS = 6
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_DEFAULT_WAIT_SECONDS = 5.0

groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))


def build_system_message(role: str) -> dict[str, str]:
    today_date = dt.date.today()
    today = f"{today_date.isoformat()} ({today_date.strftime('%A')})"
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
            "When the user refers to a relative day ('tomorrow', 'next Wednesday', "
            "'this Friday', etc.), work out the exact ISO date yourself from "
            "today's date above and pass that resolved date to tools. Do this "
            "silently — never show this calculation, or any other internal "
            "reasoning about what to do next, in your reply. Every reply must be "
            "either a tool call or a direct message to the user (a question, a "
            "tool result explained in plain language, or a final answer) — "
            "never both narration and a tool call in the same reply, and never "
            "raw text like 'function=...' or a tool name in your reply. "
            f"{audience} "
            "Use the available tools to look up real data and take real actions — "
            "never guess availability, stats, or booking outcomes. If a tool call "
            "fails or returns an error, explain the problem to the user in plain "
            "language rather than pretending it succeeded. Do not call the same "
            "tool with the same arguments more than once in a turn. When a tool "
            "result includes a 'total' or count field, quote that number exactly "
            "in your answer rather than recounting or adding results yourself. "
            "If a tool result includes 'confirmation_email_sent' or 'sent' as "
            "false for an email, tell the user plainly that the email could not "
            "be sent (e.g. suggest checking spam, or that they can ask again "
            "later) — never promise or imply that an email is on its way unless "
            "the tool result actually confirms it was sent."
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


async def _complete_with_retry(messages: list[dict[str, Any]], llm_tools: list[dict[str, Any]]) -> Any:
    """Calls the Groq completion endpoint, retrying once on `tool_use_failed`.

    Llama on Groq occasionally emits a malformed tool-call tag as plain text
    instead of a structured tool call, which Groq rejects with a 400
    `tool_use_failed` error. This is sampling noise, not a real request
    problem, so one immediate retry clears it in the common case instead of
    failing the whole turn.
    """
    try:
        return await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=llm_tools,
            tool_choice="auto",
        )
    except BadRequestError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        code = body.get("error", {}).get("code")
        if code != "tool_use_failed":
            raise
        logger.warning("Groq tool_use_failed, retrying once")
        return await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=llm_tools,
            tool_choice="auto",
        )


def _retry_after_seconds(exc: RateLimitError) -> float:
    """Reads Groq's `retry-after` response header so we wait exactly as long
    as the provider says is needed, instead of guessing or giving up
    immediately. Falls back to a short fixed wait if the header is missing
    or unparseable.
    """
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    if not header:
        return RATE_LIMIT_DEFAULT_WAIT_SECONDS
    try:
        return max(float(header), 0.0)
    except ValueError:
        return RATE_LIMIT_DEFAULT_WAIT_SECONDS


async def _get_completion(messages: list[dict[str, Any]], llm_tools: list[dict[str, Any]]) -> Any | None:
    """Runs one completion call for the loop below, retrying on 429s using
    Groq's own retry-after guidance rather than failing on the first hit.

    Returns the completion response, or None if the call ultimately failed
    (a user-facing message has already been appended to `messages` in that
    case, so the caller just needs to stop the loop).
    """
    for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
        try:
            return await _complete_with_retry(messages, llm_tools)
        except RateLimitError as exc:
            if attempt == RATE_LIMIT_MAX_RETRIES:
                logger.exception("Groq completion request rate-limited after retries")
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "The assistant is temporarily rate-limited by the LLM "
                            "provider — please try again in a few minutes."
                        ),
                    }
                )
                return None
            wait_s = _retry_after_seconds(exc)
            logger.warning(
                "Groq rate-limited (attempt %d/%d), waiting %.1fs before retry",
                attempt + 1,
                RATE_LIMIT_MAX_RETRIES,
                wait_s,
            )
            await asyncio.sleep(wait_s)
        except Exception:
            # A malformed tool call, provider outage, etc. must not crash
            # the whole request (and, worse, the shared MCP session's
            # connection) — surface a normal assistant reply instead so the
            # frontend gets a clean response to show the user.
            logger.exception("Groq completion request failed")
            messages.append(
                {
                    "role": "assistant",
                    "content": "Sorry, I ran into a problem processing that — could you try rephrasing?",
                }
            )
            return None
    return None


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
            response = await _get_completion(messages, llm_tools)
            if response is None:
                break

            msg = response.choices[0].message
            assistant_dict = _assistant_message_to_dict(msg)

            tool_calls = assistant_dict.get("tool_calls")
            if not tool_calls:
                pseudo_calls = _extract_pseudo_tool_calls(assistant_dict.get("content"))
                if pseudo_calls:
                    logger.warning(
                        "Recovered pseudo tool call(s) emitted as plain text: %s",
                        [c["function"]["name"] for c in pseudo_calls],
                    )
                    assistant_dict["tool_calls"] = pseudo_calls
                    assistant_dict["content"] = None
                    tool_calls = pseudo_calls

            messages.append(assistant_dict)

            if not tool_calls:
                break

            for call in tool_calls:
                tool_name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
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
                        "tool_call_id": call["id"],
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
