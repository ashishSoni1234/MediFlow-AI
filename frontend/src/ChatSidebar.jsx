import { useEffect, useState } from "react";
import { getSessions, renameSession, deleteSession } from "./api";
import SessionMenu from "./SessionMenu";

function formatWhen(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// Shared by PatientChat and DoctorDashboard: "New Chat" + a list of this
// user's past sessions (from GET /sessions), refetched whenever refreshKey
// changes (the parent bumps it after each turn, since that's when a new
// session's title/updated_at first becomes visible server-side). Each row
// also carries a 3-dot menu for renaming/deleting that chat.
export default function ChatSidebar({ currentSessionId, onSelect, onNewChat, onSessionDeleted, refreshKey }) {
  const [sessions, setSessions] = useState([]);

  useEffect(() => {
    let cancelled = false;
    getSessions()
      .then((list) => {
        if (!cancelled) setSessions(list);
      })
      .catch(() => {
        // best-effort; sidebar just stays empty/stale on failure
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  async function handleRename(sessionId, title) {
    await renameSession(sessionId, title);
    setSessions((list) => list.map((s) => (s.session_id === sessionId ? { ...s, title } : s)));
  }

  async function handleDelete(sessionId) {
    await deleteSession(sessionId);
    setSessions((list) => list.filter((s) => s.session_id !== sessionId));
    if (sessionId === currentSessionId) onSessionDeleted?.(sessionId);
  }

  return (
    <div className="panel chat-sidebar">
      <button type="button" className="new-chat" onClick={onNewChat}>
        + New Chat
      </button>
      <ul className="session-list">
        {sessions.map((s) => (
          <li key={s.session_id} className="session-item">
            <button
              type="button"
              className={`session-select${s.session_id === currentSessionId ? " active" : ""}`}
              onClick={() => onSelect(s.session_id)}
            >
              <span className="session-title">{s.title || "New chat"}</span>
              <span className="session-time">{formatWhen(s.updated_at)}</span>
            </button>
            <SessionMenu session={s} onRename={handleRename} onDelete={handleDelete} />
          </li>
        ))}
        {sessions.length === 0 && <li className="muted session-empty">No past chats yet.</li>}
      </ul>
    </div>
  );
}
