import { useEffect, useState } from "react";
import { getSessions, renameSession, deleteSession, deleteAllSessions } from "./api";
import SessionMenu from "./SessionMenu";

function formatWhen(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// Every past conversation for this user, each renameable/deletable via its
// 3-dot menu (same component the sidebar uses), plus a "Clear all" escape
// hatch for wiping the whole history in one go.
export default function PromptHistory() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [confirmClearAll, setConfirmClearAll] = useState(false);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSessions()
      .then((list) => {
        if (!cancelled) setSessions(list);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleRename(sessionId, title) {
    await renameSession(sessionId, title);
    setSessions((list) => list.map((s) => (s.session_id === sessionId ? { ...s, title } : s)));
  }

  async function handleDelete(sessionId) {
    await deleteSession(sessionId);
    setSessions((list) => list.filter((s) => s.session_id !== sessionId));
  }

  async function handleClearAll() {
    setClearing(true);
    try {
      await deleteAllSessions();
      setSessions([]);
      setConfirmClearAll(false);
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="panel history-panel">
      <div className="history-header">
        <h2>Chat History</h2>
        {sessions.length > 0 &&
          (confirmClearAll ? (
            <div className="clear-all-confirm">
              <span>Delete every conversation?</span>
              <button type="button" className="history-clear-all" onClick={handleClearAll} disabled={clearing}>
                {clearing ? "Clearing…" : "Yes, clear all"}
              </button>
              <button type="button" className="clear-all-cancel" onClick={() => setConfirmClearAll(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <button type="button" className="history-clear-all" onClick={() => setConfirmClearAll(true)}>
              Clear all
            </button>
          ))}
      </div>

      {loading && <p className="muted">Loading…</p>}
      {!loading && sessions.length === 0 && <p className="muted">No conversations yet.</p>}

      <ul className="history-session-list">
        {sessions.map((s) => (
          <li key={s.session_id} className="history-session-item">
            <div className="history-session-main">
              <span className="session-title">{s.title || "New chat"}</span>
              <span className="session-time">{formatWhen(s.updated_at)}</span>
            </div>
            <SessionMenu session={s} onRename={handleRename} onDelete={handleDelete} />
          </li>
        ))}
      </ul>
    </div>
  );
}
