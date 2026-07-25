# MediFlow-AI

An AI clinic assistant built as a genuine **MCP client/server pair**: a FastMCP server
exposes tools/resources/prompts over the Model Context Protocol, and a separate FastAPI
agent service acts as the MCP client, discovering those tools at runtime and letting a
Groq-hosted Llama model decide which ones to call.

```
React Frontend  ──HTTP──▶  Agent Service (FastAPI)  ──MCP (streamable-HTTP)──▶  MCP Server (FastMCP)
(chat + dashboard)          = MCP CLIENT                                          = MCP SERVER
                             - loads conversation history (Postgres)               - tools (actions)
                             - calls Groq LLM with tools discovered                 - resources (reads)
                               via tools/list                                      - prompts (templates)
                             - executes tool_calls via                             - talks to Postgres,
                               session.call_tool(...)                                Google Calendar, Gmail
```

## Why this satisfies the MCP requirement

- **No direct backend function calls.** `agent_service/` never imports anything from
  `mcp_server/`. The only way it reaches a tool is `ClientSession.call_tool(name, args)`
  over a real streamable-HTTP connection to a separately-run process
  ([mcp_client.py](agent_service/mcp_client.py)).
- **Dynamic discovery.** Every turn, [groq_orchestrator.py](agent_service/groq_orchestrator.py)
  calls `session.list_tools()` and builds the Groq tool schema from the response — there
  is no hardcoded tool-definition dict anywhere in the agent code.
- **LLM-driven orchestration.** There is no `if "book" in text` routing. The loop just
  forwards whatever `tool_calls` Groq returns to `call_tool` and feeds results back —
  see the `while` loop in `run_agent_turn`.
- **Clear separation**: `mcp_server/` (tools, resources, prompts, DB/Calendar/Email
  access) and `agent_service/` (LLM orchestration, MCP client, session store) are two
  independently-runnable processes, not one `main.py`.

## Repo layout

```
mcp_server/       FastMCP app — tools, resources, prompts (the MCP "server")
  server.py         tool/resource/prompt registrations
  db.py             Postgres access layer
  google_calendar.py  Google Calendar event creation (OAuth refresh-token flow)
  email_service.py    SMTP confirmation emails
agent_service/    FastAPI app — the MCP "client" + Groq orchestration
  main.py            HTTP API for the frontend
  mcp_client.py       wraps ClientSession / tool discovery / result formatting
  groq_orchestrator.py  the tool-calling loop
  session_store.py    conversation history, keyed by session_id (Postgres)
  notifications_store.py  direct reads for the dashboard notifications panel
frontend/         React (Vite) chat UI + doctor dashboard
db/               schema.sql, seed.sql
docker-compose.yml  Postgres container for local dev
```

## MCP primitives exposed by the server

**Tools** (actions): `check_doctor_availability`, `book_appointment`,
`send_appointment_confirmation_email`, `get_appointment_stats`, `notify_doctor`,
`reschedule_appointment`.

**Resources** (read-only, addressable, not "called" as actions):
`doctor-schedule://{doctor_name}/{date}`, `appointment://{appointment_id}`. The
dashboard's schedule view reads these directly (`GET /doctor/schedule`) with no LLM
involved — resources are for context reads, tools are for actions.

**Prompt**: `summarize_doctor_day(doctor_name, date)` — a server-defined template. The
dashboard's "Get Today's Summary" button calls `POST /agent/summary`, which fetches this
prompt via a real `prompts/get` call and runs the returned text through the same
tool-calling loop as any other message.

## Setup

### 1. Postgres

This repo ships a `docker-compose.yml` (Postgres on host port **5433**, to avoid
clashing with any other local Postgres on 5432):

```bash
docker compose up -d
docker exec -i mediflow-postgres psql -U mediflow -d mediflow < db/schema.sql
docker exec -i mediflow-postgres psql -U mediflow -d mediflow < db/seed.sql
```

(No Docker? Point `DATABASE_URL` in `.env` at any Postgres instance and run the same
two `psql` files against it directly.)

### 2. Environment variables

```bash
cp .env.example .env
```

Fill in at minimum `GROQ_API_KEY` (free tier at console.groq.com/keys). Google Calendar
and SMTP are optional — `book_appointment` and
`send_appointment_confirmation_email` degrade gracefully (booking still succeeds in
Postgres; the tool just reports that the calendar/email step was skipped) when those
env vars are blank, so you can demo the whole flow without setting them up.

### 3. Python environment (shared by both services)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\pip install -r mcp_server/requirements.txt -r agent_service/requirements.txt
# macOS/Linux:
.venv/bin/pip install -r mcp_server/requirements.txt -r agent_service/requirements.txt
```

### 4. Frontend

```bash
cd frontend
npm install
cp .env.example .env   # or create .env with VITE_AGENT_SERVICE_URL=http://localhost:8200
```

## Running (three separate processes)

```bash
# Terminal 1 — MCP server (port 8100)
.venv\Scripts\python mcp_server\server.py

# Terminal 2 — Agent service (port 8200)
.venv\Scripts\python agent_service\main.py

# Terminal 3 — Frontend (port 5173)
cd frontend && npm run dev
```

Verify the MCP server independently before touching the agent, using the official
[MCP Inspector](https://github.com/modelcontextprotocol/inspector) CLI:

```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8100/mcp --method tools/list
npx @modelcontextprotocol/inspector --cli http://localhost:8100/mcp --method tools/call \
  --tool-name check_doctor_availability --tool-arg doctor_name="Ahuja" --tool-arg date="today"
```

## Sample prompts

Patient chat (try these in order — the second is a genuine multi-turn follow-up):

- "Check Dr. Ahuja's availability for tomorrow morning."
- "Book me with Dr. Ahuja tomorrow at 10am, I'm Jane Doe, jane@example.com, reason: checkup."
- "How many patients came in with fever this week?"

Doctor dashboard:

- Click **Get Today's Summary** for any doctor (exercises the `summarize_doctor_day` prompt).
- "How many appointments do I have today and tomorrow?"
- "Notify me if any patient today has a fever." (the LLM decides whether/how to call `notify_doctor`)

## Architecture decisions

- **Streamable-HTTP over stdio.** The MCP server runs as its own long-lived process
  serving `http://localhost:8100/mcp`, so it can be restarted, load-balanced, or hosted
  independently of the agent — matches "genuinely separate service" better than spawning
  it as a stdio subprocess of the agent.
- **Groq + Llama 3.3 70B.** OpenAI-compatible `tools=[...]` function-calling API, so
  converting an MCP `inputSchema` into a Groq tool schema is a direct passthrough
  (`mcp_client.tool_to_groq_schema`).
- **One shared Postgres, two independent access layers.** `mcp_server/db.py` and
  `agent_service/session_store.py` / `notifications_store.py` intentionally don't share
  code — the agent service's direct Postgres reads (session history, notifications) are
  ordinary application plumbing for the UI, not tool execution on the LLM's behalf, so
  they're exempt from the "must go through MCP" rule (which only applies to actions the
  LLM decides to take).
- **Graceful degradation for Calendar/Email.** Postgres is the appointment system of
  record; Google Calendar and email are best-effort layers on top. Missing credentials
  produce a clear tool result the LLM can relay in plain language, rather than an
  unhandled exception.
