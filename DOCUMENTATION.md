# MediFlow-AI — Design & Architecture Documentation

> Companion deep-dive to the [README](README.md). The README gets you running and shows sample prompts; this document explains **why the system is built the way it is**.

**Live app:** https://medi-flow-ai-six.vercel.app/

---

## Table of Contents

1. [What MediFlow-AI Does](#1-what-mediflow-ai-does)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Tech Stack](#3-tech-stack)
4. [Component Breakdown](#4-component-breakdown)
5. [Data Model](#5-data-model)
6. [MCP Primitives (Tools, Resources, Prompts)](#6-mcp-primitives-tools-resources-prompts)
7. [The Agent Loop — How the LLM Decides What To Do](#7-the-agent-loop--how-the-llm-decides-what-to-do)
8. [Authentication & Authorization](#8-authentication--authorization)
9. [End-to-End Request Flow: Booking an Appointment](#9-end-to-end-request-flow-booking-an-appointment)
10. [Deployment Topology](#10-deployment-topology)
11. [Key Design Decisions & Trade-offs](#11-key-design-decisions--trade-offs)
12. [Reliability & Failure Handling](#12-reliability--failure-handling)

---

## 1. What MediFlow-AI Does

MediFlow-AI is an AI clinic-assistant web app with two user roles:

- **Patients** chat in plain English to check a doctor's availability and book an appointment — no forms, no dropdowns.
- **Doctors** get a dashboard with a one-click "Get Today's Summary" button, an in-app notification feed, and their own natural-language chat box for questions like "how many patients came in with fever this week?"

Everything a patient or doctor asks for is answered by an LLM (Groq-hosted Llama) that dynamically discovers a set of backend **tools** over the **Model Context Protocol (MCP)** and decides, per message, which tool(s) to call — there is no hardcoded `if "book" in text` routing anywhere in the app.

## 2. High-Level Architecture

MediFlow-AI is a genuine **MCP client/server pair**, plus a React frontend, split into three independently-runnable processes:

```
┌─────────────────────┐        HTTPS (REST/JSON)        ┌──────────────────────────┐
│   React Frontend     │ ───────────────────────────────▶ │   Agent Service (FastAPI) │
│   (Vite, deployed     │ ◀─────────────────────────────── │   = the MCP CLIENT        │
│   on Vercel)          │                                   │                          │
│  - Patient chat       │                                   │  - JWT auth (signup/login/│
│  - Doctor dashboard    │                                   │    Google via Supabase)   │
│  - Session sidebar     │                                   │  - Conversation history   │
│  - Prompt history      │                                   │    (Postgres)             │
└─────────────────────┘                                   │  - Groq LLM tool-calling  │
                                                             │    loop                   │
                                                             │  - notifications, session │
                                                             │    CRUD (direct Postgres) │
                                                             └────────────┬─────────────┘
                                                                          │
                                                          MCP over streamable-HTTP
                                                          (tools/list, tools/call,
                                                           resources/read, prompts/get)
                                                                          │
                                                                          ▼
                                                             ┌──────────────────────────┐
                                                             │   MCP Server (FastMCP)     │
                                                             │   = the MCP SERVER         │
                                                             │                            │
                                                             │  Tools (actions):          │
                                                             │   check_doctor_availability│
                                                             │   book_appointment         │
                                                             │   reschedule_appointment   │
                                                             │   suggest_reschedule       │
                                                             │   get_appointment_stats    │
                                                             │   notify_doctor            │
                                                             │   send_appointment_        │
                                                             │     confirmation_email     │
                                                             │                            │
                                                             │  Resources (reads):        │
                                                             │   doctor-schedule://...     │
                                                             │   appointment://...         │
                                                             │                            │
                                                             │  Prompt (template):        │
                                                             │   summarize_doctor_day     │
                                                             └──────┬──────┬──────┬───────┘
                                                                    │      │      │
                                                            ┌───────▼┐ ┌───▼───┐ ┌▼──────────┐
                                                            │Postgres│ │Google │ │Brevo email │
                                                            │(source │ │Calendar│ │API + Slack │
                                                            │of truth)│ │(OAuth) │ │webhook     │
                                                            └────────┘ └───────┘ └───────────┘
```

**Why a client/server split instead of one backend?** The assignment requirement (and good practice) is that the LLM never gets hardcoded, privileged access to business logic — it only gets what the MCP server chooses to expose as a tool schema. Concretely:

- `agent_service/` **never imports anything from `mcp_server/`**. The only way it reaches a tool is `ClientSession.call_tool(name, args)` over a real network connection ([mcp_client.py](agent_service/mcp_client.py)) to a separately-running process.
- Every turn, [groq_orchestrator.py](agent_service/groq_orchestrator.py) calls `session.list_tools()` and builds the Groq tool schema **from that response** — there is no hardcoded tool-definition dict in the agent code. Add a tool on the server and the agent picks it up on the next request with zero client-side changes.
- The two processes can be deployed, restarted, and scaled independently.

## 3. Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | **React 19** + **Vite 8** | Plain CSS (`App.css`, `index.css`), no UI framework |
| Frontend auth | **Supabase JS SDK** | Used only for "Sign in with Google" — Supabase issues the OAuth token, the backend verifies it |
| Agent service | **FastAPI** (Python, async) | HTTP API consumed by the frontend; also the MCP *client* |
| LLM orchestration | **Groq API** — `llama-3.1-8b-instant` (configurable via `GROQ_MODEL`) | OpenAI-compatible tool-calling (`tools=[...]`, `tool_choice="auto"`) |
| MCP layer | **`mcp` Python SDK** (`FastMCP`), **streamable-HTTP** transport | Server exposes tools/resources/prompts; client (`ClientSession`) discovers and calls them |
| MCP server | **FastMCP** (Python, async) | Independent process; owns all business logic and external integrations |
| Database | **PostgreSQL 16** (`asyncpg`) | Two independent access layers: `mcp_server/db.py` and `agent_service/session_store.py` / `notifications_store.py` / `users_store.py` / `patients_store.py` / `doctors_store.py` |
| Auth | **bcrypt** (password hashing) + **PyJWT** (session tokens) | Role (`patient`/`doctor`) embedded in the JWT claims |
| Calendar | **Google Calendar API** (OAuth2 refresh-token flow) | Best-effort event creation on booking |
| Email | **Brevo (Sendinblue) transactional email API** (HTTPS, not SMTP) | SMTP is blocked outbound on Render, so email goes over Brevo's REST API |
| Notifications | Postgres `notifications` table (+ optional **Slack incoming webhook**) | In-app feed on the doctor dashboard |
| Local dev infra | **Docker Compose** (Postgres only) | Frontend/agent/MCP server run as plain local processes |
| Testing | **pytest**, `pytest-asyncio` | Unit tests for auth, authorization, orchestrator behavior, availability math, tool contracts |
| Hosting (deployed demo) | **Vercel** (frontend static build) + a Python host (e.g. Render) for `agent_service`/`mcp_server`, managed Postgres | See [§10 Deployment Topology](#10-deployment-topology) |

## 4. Component Breakdown

### 4.1 `frontend/` — React + Vite SPA

| File | Responsibility |
|---|---|
| `App.jsx` | Top-level shell: auth gate, header/nav, tab switch between chat/dashboard and prompt history |
| `AuthPage.jsx` | Login/signup form, role picker (patient/doctor), Google sign-in button |
| `PatientChat.jsx` | Patient's conversational booking UI |
| `DoctorDashboard.jsx` | Doctor's schedule view, "Get Today's Summary" button, chat box, notification bell |
| `ChatSidebar.jsx` | List of past sessions (rename/delete), like a chat-app conversation list |
| `SessionMenu.jsx` | Per-session context menu (rename/delete actions) |
| `PromptHistory.jsx` | Flat, timestamped log of every prompt the signed-in user has typed |
| `api.js` | Thin `fetch` wrapper — attaches the JWT, centralizes error handling |
| `auth.js` | Token storage (`localStorage`) and current-user decode helpers |
| `session.js` | Client-side session-id generation/management helpers |
| `supabaseClient.js` | Supabase client instance, used only for Google OAuth |

The frontend talks to exactly one backend base URL, `VITE_AGENT_SERVICE_URL` — it never talks to the MCP server directly.

### 4.2 `agent_service/` — FastAPI app (the MCP *client*)

| File | Responsibility |
|---|---|
| `main.py` | All HTTP routes: auth, `/agent/message`, `/agent/summary`, `/doctor/schedule`, `/appointment/{id}`, notifications, sessions, prompt history |
| `groq_orchestrator.py` | The tool-calling loop: builds the system prompt, calls Groq, forwards `tool_calls` to the MCP session, feeds results back, repeats until a final answer |
| `mcp_client.py` | Wraps `ClientSession` / `streamablehttp_client`; converts an MCP `Tool` schema to a Groq function schema; flattens `CallToolResult` content into text |
| `auth.py` | bcrypt hashing, JWT issuance/verification, Supabase Google-token verification, `get_current_user` / `require_doctor` FastAPI dependencies |
| `session_store.py` | Conversation history persistence (`conversation_sessions` table), prompt log, session CRUD |
| `notifications_store.py` | Direct reads/writes for the doctor dashboard's notification panel |
| `users_store.py`, `patients_store.py`, `doctors_store.py` | Postgres access for the `users`, `patients`, `doctors` tables |

### 4.3 `mcp_server/` — FastMCP app (the MCP *server*)

| File | Responsibility |
|---|---|
| `server.py` | Registers every `@mcp.tool()`, `@mcp.resource()`, and `@mcp.prompt()`; date-parsing helpers (`today`, `tomorrow`, weekday names); optional shared-secret auth middleware |
| `db.py` | Postgres access layer: doctor lookup/fuzzy-match, free-slot computation, atomic booking with a per-doctor advisory lock (double-booking prevention), appointment stats |
| `google_calendar.py` | Creates a Calendar event via OAuth2 refresh-token flow; no-ops gracefully if credentials are absent |
| `email_service.py` | Sends transactional email via the Brevo HTTPS API; `is_configured()` lets the caller report *why* an email wasn't sent |
| `email_templates.py` | Builds the plain-text + HTML confirmation email body |

### 4.4 `db/` — schema and seed data

- `schema.sql` — all tables, indexes, and small self-healing `ALTER`/`DO` blocks for older databases.
- `seed.sql` — 3 demo doctors (with a shared bcrypt-hashed demo password), 5 demo patients, and a spread of appointments computed relative to `CURRENT_DATE` so "today"/"this week" sample prompts always have data.

## 5. Data Model

```
doctors ─┬─< appointments >─┬─ patients
         │                  │
         └─< notifications  │
                             │
users (role: patient|doctor, linked_id → patients.id or doctors.id)

conversation_sessions (session_id PK, role, user_ref, title, history JSONB)
prompt_log (flat append-only log of every typed prompt)
```

| Table | Purpose |
|---|---|
| `doctors` | Doctor identity + working hours (used to compute free slots) |
| `patients` | Patient identity, created/looked-up by email during booking |
| `users` | Login credentials for **both** roles (`role` + `linked_id` disambiguate) — kept separate from `doctors`/`patients` so the same email/password mechanism serves both |
| `appointments` | Source of truth for bookings; `google_calendar_event_id` links to the best-effort Calendar side-effect |
| `conversation_sessions` | Full LLM message history (system/user/assistant/tool messages) per session, reloaded every turn so the model sees the whole thread |
| `prompt_log` | Separate, timestamped, append-only record of raw user text — powers the "History" tab without parsing the LLM message array |
| `notifications` | Doctor-facing in-app (or Slack-mirrored) notification feed |

## 6. MCP Primitives (Tools, Resources, Prompts)

MCP distinguishes three kinds of server-exposed capability, and this project deliberately uses all three for their intended purpose:

### Tools (actions — the LLM chooses to invoke these)

| Tool | Purpose |
|---|---|
| `check_doctor_availability(doctor_name, date, time_window?)` | Computes free slots from working hours minus existing bookings |
| `book_appointment(doctor_name, patient_name, patient_email, datetime_iso, reason, duration_minutes?)` | Validates required fields, atomically double-books-checks + inserts, best-effort creates a Calendar event, notifies the doctor, sends a confirmation email |
| `reschedule_appointment(appointment_id, new_datetime_iso)` | Atomically moves an existing appointment |
| `suggest_reschedule(doctor_name, originally_requested_datetime)` | Returns up to 3 real free slots near a rejected time — used after a failed booking instead of letting the LLM guess |
| `get_appointment_stats(doctor_name?, date_range, filter_reason?)` | Counts/lists appointments over a range, optionally filtered by reason substring — powers both patient and doctor reporting questions |
| `notify_doctor(doctor_name, message, channel)` | Writes an in-app notification, optionally mirrors it to Slack |
| `send_appointment_confirmation_email(appointment_id)` | Manual resend, pulling stored appointment data rather than re-typed arguments |

### Resources (read-only, addressable by URI — never "called" as an action)

- `doctor-schedule://{doctor_name}/{date}` — raw appointment list for a day; the dashboard's schedule view reads this directly (`GET /doctor/schedule`) with **no LLM involved**.
- `appointment://{appointment_id}` — raw single-appointment details; backs `GET /appointment/{id}`.

### Prompt (a server-defined template)

- `summarize_doctor_day(doctor_name, date)` — returns instruction text (not a canned answer). The dashboard's "Get Today's Summary" button calls `POST /agent/summary`, which does a real `prompts/get`, appends the returned text as a message, and runs it through the *same* tool-calling loop as any chat message — then delivers the result via the `notify_doctor` tool (in-app channel) so Scenario 2 (report) and Scenario 1 (booking) demonstrably use two different delivery channels.

## 7. The Agent Loop — How the LLM Decides What To Do

`groq_orchestrator.run_agent_turn()` ([agent_service/groq_orchestrator.py](agent_service/groq_orchestrator.py)):

1. Opens an MCP session and calls `session.list_tools()` — the tool schema sent to Groq is built from this live response, not a static file.
2. Sends the full message history + tool schemas to Groq (`tool_choice="auto"`).
3. If Groq's response includes `tool_calls`, each one is executed via `session.call_tool(name, args)` against the MCP server, and the result is appended back into the message list as a `role: "tool"` message.
4. Loops (up to `MAX_TOOL_ROUNDS = 6`) until the model responds with plain content instead of a tool call, then returns.
5. A dedicated regex-based recovery step (`_extract_pseudo_tool_calls`) catches a known Llama-on-Groq failure mode where a tool call is emitted as literal text (e.g. `<function=book_appointment>{...}`) instead of a structured `tool_calls` entry, and re-executes it as a real tool call instead of leaking the raw tag to the user.
6. Rate limits (`RateLimitError`) are retried using Groq's own `retry-after` header; a `tool_use_failed` 400 is retried once; any other failure degrades to a plain "please rephrase" assistant message rather than a 500.

The **system prompt** (`build_system_message`) is where behavioral guardrails live — e.g. "never invent a placeholder value for patient name/email/reason," "copy the exact ISO string from `free_slots` rather than recomputing dates yourself," "only report an email as sent if the tool result confirms it." These rules exist because early iterations of the agent exhibited exactly the failure they now prevent (see commit history) — the fix is a prompt constraint plus, where possible, a server-side validation (`_validate_booking_fields` in `mcp_server/server.py`) so the LLM can't bypass it by ignoring the instruction.

## 8. Authentication & Authorization

- **Signup/login**: bcrypt-hashed passwords, JWT (`HS256`) issued on success, 7-day expiry. Claims: `user_id`, `role`, `linked_id`, `name`.
- **Google sign-in**: the frontend uses Supabase Auth to obtain a Google-verified access token; the backend calls Supabase's `/auth/v1/user` endpoint to verify it (no need to hold the Supabase project's JWT secret), then provisions/looks up a local user exactly like manual signup would.
- **Doctor signup is gated**: only pre-seeded doctor emails (`db/seed.sql`) can register with `role: "doctor"` — prevents a random signup from impersonating a doctor.
- **Every protected route** depends on `get_current_user` (`Authorization: Bearer <jwt>`) or `require_doctor`; nothing trusts a client-supplied `role`/`user_ref` field.
- **Session ownership checks**: `/agent/message`, `/agent/summary`, session history/rename/delete all verify the session's stored `(role, user_ref)` matches the caller before acting.
- **Appointment resource access** (`GET /appointment/{id}`) requires the caller to be the patient or the doctor on that specific appointment.
- **Per-process in-memory rate limiting** on `/auth/signup` and `/auth/login` (10 attempts / 5 minutes per client IP) — adequate for a single-instance demo, not a distributed limiter.
- **Optional MCP-layer auth**: `MCP_SHARED_SECRET`, when set, requires `Authorization: Bearer <secret>` on every request to the MCP server (`_SharedSecretMiddleware`) — relevant if the MCP server's port is ever reachable from outside a private network.

## 9. End-to-End Request Flow: Booking an Appointment

1. Patient types "Book me with Dr. Ahuja tomorrow at 10am, I'm Jane Doe, jane@example.com, reason: checkup." in `PatientChat.jsx`.
2. Frontend calls `POST /agent/message` with `{session_id, text}` and the JWT.
3. `agent_service/main.py` verifies the JWT, checks session ownership, logs the raw prompt (`prompt_log`), loads/refreshes history, appends the user message.
4. `run_agent_turn` opens an MCP session, discovers tools, sends everything to Groq.
5. Groq returns a `tool_calls` entry for `book_appointment` with the extracted arguments.
6. The orchestrator calls `session.call_tool("book_appointment", {...})` — a real MCP `tools/call` JSON-RPC request over streamable-HTTP to the MCP server process.
7. `mcp_server/server.py`'s `book_appointment`:
   - Validates the fields aren't blank/placeholder values.
   - Resolves the doctor by fuzzy name match.
   - Atomically checks-and-inserts under a per-doctor Postgres advisory lock (`db.book_appointment_atomic`) so two near-simultaneous bookings can't both slip through.
   - Best-effort creates a Google Calendar event.
   - Inserts an in-app doctor notification.
   - Best-effort sends a Brevo confirmation email.
   - Returns a structured result including `confirmation_email_sent` and `calendar_event_created` booleans reflecting what *actually* happened.
8. The tool result is appended to the message list as a `role: "tool"` message; Groq is called again and produces a plain-language final reply that must accurately reflect those booleans (enforced by the system prompt).
9. `agent_service` persists the updated history and returns `{session_id, reply}` to the frontend, which renders it in the chat.

## 10. Deployment Topology

The live demo (https://medi-flow-ai-six.vercel.app/) is split across providers, matching the three-process architecture:

- **Frontend** — static Vite build deployed to **Vercel**, configured with `VITE_AGENT_SERVICE_URL` pointing at the hosted agent service.
- **Agent service & MCP server** — each runs as its own web process (e.g. on Render or any host that can run a long-lived Python/uvicorn process), bound to `$PORT` per the platform's convention, communicating with each other over `MCP_SERVER_URL` (streamable-HTTP).
- **Postgres** — a managed instance (connection string in `DATABASE_URL`); locally this is the `docker-compose.yml` container instead.
- **Supabase** — used only as the Google OAuth identity provider for the frontend's "Sign in with Google" button; no application data lives there.
- **Brevo / Google Calendar / Slack** — external integrations reached over HTTPS from the MCP server; all optional and degrade gracefully when unconfigured.

CORS on the agent service (`CORS_ORIGINS`) must include the deployed frontend origin for the hosted app to work end-to-end.

## 11. Key Design Decisions & Trade-offs

- **Streamable-HTTP over stdio for MCP.** The MCP server is a standalone long-lived process (`http://host:8100/mcp`) rather than a stdio subprocess spawned by the agent — it can be restarted, load-balanced, or deployed independently, which better matches "genuinely separate service" than an in-process subprocess model.
- **Groq + a small Llama model.** `llama-3.1-8b-instant` is used instead of a 70B model specifically because this app's tool-calling loop issues several completions per user turn, and the 8B model's free-tier rate limits are far more forgiving for that access pattern — a direct trade of some reasoning quality for reliability under Groq's free tier.
- **One shared Postgres, two independent access layers.** `mcp_server/db.py` and the agent service's stores intentionally don't share code. The agent's direct reads (session history, notifications, prompt log) are ordinary application plumbing for the UI, not "actions taken on the LLM's behalf" — so they're exempt from the "must go through MCP" rule, which only governs what the LLM itself can decide to do.
- **Deterministic date parsing, not LLM arithmetic.** Weekday names ("next Wednesday") are resolved in `mcp_server/server.py`'s `_parse_date`, not by asking the small model to compute an ISO date — an earlier version let the model do this and it silently booked "Wednesday" on the current Monday. The system prompt now explicitly tells the model to pass weekday phrases through as-is.
- **Server-side booking side effects, not LLM-orchestrated ones.** Doctor notification and confirmation email on a successful booking happen unconditionally inside `book_appointment`, rather than being left as separate tool calls the LLM has to remember to make — this is why doctors reliably see a notification for every booking regardless of how the conversation went.
- **Graceful degradation for Calendar/Email.** Postgres is the appointment system of record; Google Calendar and email are best-effort layers on top. Missing credentials or a provider failure produce a clear, truthful tool result (`calendar_event_created: false`, `confirmation_email_sent: false`) that the LLM must relay honestly, rather than an unhandled exception or a false claim of success.
- **Resources vs. tools, used as MCP intends.** Read-only, addressable data (a day's schedule, a single appointment) is exposed as MCP *resources* and read directly by the dashboard with no LLM in the loop; only state-changing or decision-requiring operations are *tools* the LLM invokes.

## 12. Reliability & Failure Handling

| Failure mode | Handling |
|---|---|
| Groq rate limit (429) | Retried using the provider's own `retry-after` header, up to 2 attempts, before surfacing a clear "temporarily rate-limited" message |
| Malformed tool-call (`tool_use_failed`) | One immediate retry |
| Tool call emitted as literal text instead of structured `tool_calls` | Recovered via regex + real JSON parsing (`_extract_pseudo_tool_calls`) and re-executed, instead of leaking raw text to the user |
| Double-booking race | Prevented at the database layer with a per-doctor Postgres advisory lock around the check-then-insert |
| Google Calendar failure | Caught and logged; booking still succeeds (Postgres is the source of truth) |
| Email provider failure/misconfiguration | Caught and logged; `confirmation_email_sent: false` is reported truthfully instead of assumed success |
| Unbounded tool-calling loop | Hard cap at `MAX_TOOL_ROUNDS = 6`, after which the agent asks the user to clarify rather than looping forever |
| Booking with missing/placeholder patient info | Rejected server-side (`_validate_booking_fields`) even if the LLM tries to pass through a placeholder — defense in depth beyond the system-prompt instruction |
