# MediFlow AI — Demo Video Script (English)

Total runtime target: 6–8 minutes. Read narration lines naturally, don't sound robotic — pause after typing a prompt to let the agent's reply render before you speak again.

---

## 0. Before You Hit Record — Checklist

Run these three processes and confirm each is healthy before recording (a crash mid-take is the #1 reason demo videos get re-shot):

1. Postgres via docker-compose (port 5433)
2. MCP server (`mcp_server`, port 8100)
3. Agent service (`agent_service`, port 8200)
4. Frontend (`npm run dev`, port 5173)

Also open a second browser tab in advance (you'll use it for the doctor login later) so you're not fumbling with tabs on camera.

**Doctor login credentials (already seeded in the DB):**
- Email: `ahuja@mediflow.example`
- Password: `mediflow123`

There is no pre-made patient login — you will sign up live during the patient section. Have a throwaway patient email ready, e.g. `priya.patel@example.com`.

---

## 1. Intro (0:00 – 0:30)

**Screen:** Landing/auth page of the app, nothing typed yet.

**Narration:**
> "Hi, this is my submission for the Full-Stack Developer Intern assignment — an agentic AI assistant for doctor appointment scheduling and reporting, built using MCP, the Model Context Protocol.
>
> The idea is simple: a patient can talk to the system in plain English to check a doctor's availability and book an appointment, and a doctor can ask natural language questions to get a summary of their day — without either of them needing to know any technical detail about how it works underneath.
>
> Let me quickly show you the architecture, then I'll walk through both flows live."

---

## 2. Quick Architecture Overview (0:30 – 1:00)

**Screen:** Optionally show a simple diagram or just narrate over the code editor with the three folders open (`frontend`, `agent_service`, `mcp_server`).

**Narration:**
> "The stack has three parts. The React frontend is the chat interface both patients and doctors use. The FastAPI agent service holds the conversation, talks to an LLM — I'm using Groq for tool-calling — and decides which tools to invoke. And the MCP server is where all the actual tools live: checking availability, booking appointments, querying appointment stats, sending emails, and creating Google Calendar events.
>
> The key point of MCP here is that the tools are discovered dynamically — the agent calls `tools/list` on the MCP server at startup, so the LLM decides on its own which tool to use and when, based on the user's prompt. I haven't hardcoded 'if user says X, call Y.'"

---

## 3. Auth Page (1:00 – 1:30)

**Screen:** The single Auth page — show it toggling between Login and Sign Up.

**Narration:**
> "Here's the login screen. It supports email/password as well as Google sign-in through Supabase. When you sign up, you pick a role — Patient or Doctor — and that role is embedded in the JWT token, so the frontend routes you to the right interface automatically. Doctor sign-up is restricted to a pre-approved list of doctor emails, so a random user can't sign up and pretend to be a doctor."

**Action:** Click "Sign Up," show the role radio buttons (Patient / Doctor), then go back to Login.

---

## 4. Patient Flow (1:30 – 4:30)

### 4.1 Sign up as a patient

**Screen:** Sign Up form.

**Narration:**
> "Since I don't have a pre-made patient account, let me sign up live as a patient."

**Action:** Fill in name, `priya.patel@example.com`, a password, role = Patient. Submit. You land on the Patient Chat screen.

**Narration:**
> "And now I'm in the patient chat interface. This is a plain conversational box — no forms, no dropdowns for picking a doctor or a time slot."

### 4.2 Prompt 1 — Check availability

**Action:** Type into the chat box:
> `Check Dr. Ahuja's availability for tomorrow morning.`

**Narration (while it loads):**
> "Behind the scenes, the LLM is reading this prompt, deciding to call the `check_doctor_availability` tool with the doctor name, the date, and the time window, and the MCP server is querying Postgres for Dr. Ahuja's existing appointments to compute free slots."

**Action:** Let the response render — it should list free slots (e.g., 9:00, 9:30, 10:00 AM etc.).

**Narration:**
> "Great, so it's telling me the open morning slots for Dr. Ahuja tomorrow."

### 4.3 Prompt 2 — Follow-up in the same session (multi-turn)

**Action:** Type:
> `Actually, can you check Friday afternoon instead?`

**Narration:**
> "Notice I didn't repeat 'Dr. Ahuja' or explain who I am again — the agent remembers the context from the previous message. This works because every message is saved with the full conversation history in Postgres and reloaded on each turn, so the LLM sees the whole thread, not just the latest line."

**Action:** Let it respond with Friday afternoon slots (e.g., 3:00 PM available).

### 4.4 Prompt 3 — Book the appointment (the actual multi-turn payoff)

**Action:** Type:
> `Please book the 3pm slot for me, my email is priya.patel@example.com, reason: fever and cold.`

**Narration (while it loads):**
> "This is the important part — I'm not restating the doctor or the day. The agent resolves 'the 3pm slot' against the slot we already discussed for Friday afternoon, then calls `book_appointment`. That tool does three things: it writes the appointment into Postgres with a double-booking check, it attempts to create a Google Calendar event, and it attempts to send a confirmation email."

**Action:** Let the response render.

**Narration — branch depending on what actually happens:**

- If email + calendar succeed:
  > "And there it is — booking confirmed, calendar event created, and confirmation email sent to my inbox."
- If email fails (SMTP not reachable, spam-filtered, etc.):
  > "Here you can see the booking itself succeeded — that's the source of truth in Postgres — but the agent is telling me honestly that the confirmation email did not go through, rather than lying and claiming it was sent. I specifically built this in: earlier the agent would claim success regardless of the real outcome, so I fixed it to only report an email as sent when the tool result actually confirms it. Booking still stands either way — email and calendar are best-effort, not blocking."

*(Keep whichever branch matches what you see live — don't script a guaranteed "email sent" if you haven't verified SMTP connectivity beforehand.)*

### 4.5 Prompt 4 — A reporting-style question from the patient side (optional, shows breadth)

**Action:** Type:
> `How many patients came in with fever this week?`

**Narration:**
> "The same reporting tool that powers the doctor's summary is also available here — the agent calls `get_appointment_stats` with a reason filter of 'fever' and a date range of this week."

---

## 5. Doctor Flow (4:30 – 7:00)

### 5.1 Log out, log in as Dr. Ahuja

**Action:** Log out (or switch to the second pre-opened tab). Log in with:
- Email: `ahuja@mediflow.example`
- Password: `mediflow123`

**Narration:**
> "Now let me switch to the doctor's side, logging in as Dr. Ahuja using a seeded demo account."

**Screen:** Doctor Dashboard loads — point out the chat box, the "Get Today's Summary" button, and the notification bell icon.

### 5.2 Dashboard button — one-click summary

**Action:** Click **"Get Today's Summary."**

**Narration:**
> "Doctors don't have to type anything if they don't want to — this button calls a dedicated `/agent/summary` endpoint, which runs an MCP prompt template called `summarize_doctor_day` through the same tool-calling loop, and delivers the result as an in-app notification."

**Action:** Click the notification bell to show the badge and the summary text appearing there.

**Narration:**
> "And here it shows up in the notification panel — this is a genuinely separate delivery channel from the email we used on the patient side, which was one of the assignment's requirements."

### 5.3 Natural language query — appointments today/tomorrow

**Action:** In the doctor's chat box, type:
> `How many appointments do I have today and tomorrow?`

**Narration:**
> "The doctor can also just ask in plain English. This calls `get_appointment_stats` twice internally — or once with a combined range — and returns a human-readable count for each day."

### 5.4 Natural language query — filtered by symptom

**Action:** Type:
> `How many patients came in with fever?`

**Narration:**
> "Same tool, different filter — this time it's matching against the appointment reason field for 'fever.'"

### 5.5 (Optional bonus) Slack-style notification

**Action:** Type:
> `Notify me on Slack if any patient today has a fever.`

**Narration:**
> "The `notify_doctor` tool actually supports multiple channels — in-app, which we saw on the button, and Slack, which I can trigger just by asking for it this way. The LLM picks the channel based on what I say."

*(Only include this if your `SLACK_WEBHOOK_URL` is live and you've verified a message actually lands in the channel — otherwise skip it.)*

---

## 6. Bonus Features Tour (7:00 – 7:45)

**Action:** Show the session sidebar (list of past conversations, rename/delete) and the "History" tab.

**Narration:**
> "A couple of extra things I added beyond the core ask: a sidebar of past chat sessions so a patient or doctor can jump between separate conversations, and a History tab that logs every prompt anyone has typed, timestamped — useful for audit or for a doctor to see what's been asked over time."

---

## 7. Closing (7:45 – 8:00)

**Screen:** Back to the architecture view or the GitHub repo page.

**Narration:**
> "So to summarize — patients can check availability and book appointments conversationally, with real multi-turn memory; doctors get both a one-click summary and free-form natural language reporting, delivered through a separate in-app notification channel; and all of the actual logic — availability, booking, stats, email, calendar, notifications — lives behind MCP tools that the LLM discovers and chooses dynamically, not through hardcoded routing. Thanks for watching — the full source is on GitHub, with setup steps in the README."

---

## Quick Reference Card (keep visible off-camera while recording)

| Item | Value |
|---|---|
| Doctor login (Dr. Ahuja) | `ahuja@mediflow.example` / `mediflow123` |
| Other seeded doctors | `mehta@mediflow.example`, `rao@mediflow.example` (same password) |
| Patient login | none seeded — sign up live, e.g. `priya.patel@example.com` |
| Patient prompt 1 | `Check Dr. Ahuja's availability for tomorrow morning.` |
| Patient prompt 2 | `Actually, can you check Friday afternoon instead?` |
| Patient prompt 3 (booking) | `Please book the 3pm slot for me, my email is priya.patel@example.com, reason: fever and cold.` |
| Patient prompt 4 | `How many patients came in with fever this week?` |
| Doctor button | Click "Get Today's Summary" |
| Doctor prompt 1 | `How many appointments do I have today and tomorrow?` |
| Doctor prompt 2 | `How many patients came in with fever?` |
| Doctor prompt 3 (optional) | `Notify me on Slack if any patient today has a fever.` |
