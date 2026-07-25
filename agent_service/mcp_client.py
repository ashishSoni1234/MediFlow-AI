"""Thin wrapper around the MCP client SDK.

This is the ONLY place in the agent service that talks MCP. It opens a
real streamable-HTTP connection to the MCP server (a separate process)
and speaks JSON-RPC over it — tool discovery and tool execution both go
through here, never as direct Python function calls into mcp_server/.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8100/mcp")
MCP_SHARED_SECRET = os.environ.get("MCP_SHARED_SECRET")


@asynccontextmanager
async def mcp_session():
    headers = {"Authorization": f"Bearer {MCP_SHARED_SECRET}"} if MCP_SHARED_SECRET else None
    async with streamablehttp_client(MCP_SERVER_URL, headers=headers) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def tool_to_groq_schema(tool: Any) -> dict:
    """Convert an MCP Tool (from tools/list) into an OpenAI/Groq-style tool schema."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


def tool_result_to_text(result: Any) -> str:
    """Flatten an MCP CallToolResult's content blocks into a string for the LLM."""
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
        else:
            parts.append(str(block))
    text = "\n".join(parts)
    if getattr(result, "isError", False):
        return f"ERROR: {text}"
    return text
