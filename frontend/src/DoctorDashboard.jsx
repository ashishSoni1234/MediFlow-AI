import { useState, useEffect, useCallback } from "react";
import { sendMessage, getDaySummary, getNotifications, markNotificationRead, getSessionHistory } from "./api";
import ChatSidebar from "./ChatSidebar";
import { getOrCreateSessionId, newSessionId } from "./session";

const LAST_SESSION_KEY_PREFIX = "mediflow_doctor_last_session_";

// `doctor` is the identity established via JWT at login (see AuthPage.jsx)
// — no client-side picker, so a signed-in doctor can only ever act as
// themselves.
export default function DoctorDashboard({ doctor, onLogout }) {
  const lastSessionKey = `${LAST_SESSION_KEY_PREFIX}${doctor.linkedId}`;
  const [sessionId, setSessionId] = useState(() => getOrCreateSessionId(lastSessionKey));
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const unreadCount = notifications.filter((n) => !n.read).length;

  useEffect(() => {
    let cancelled = false;
    getSessionHistory(sessionId)
      .then((history) => {
        if (!cancelled) setMessages(history);
      })
      .catch(() => {
        // no prior session yet, or agent service unreachable
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const refreshNotifications = useCallback(async () => {
    try {
      const list = await getNotifications(doctor.linkedId);
      setNotifications(list);
    } catch {
      // polling best-effort; ignore transient errors
    }
  }, [doctor.linkedId]);

  useEffect(() => {
    refreshNotifications();
    const interval = setInterval(refreshNotifications, 8000);
    return () => clearInterval(interval);
  }, [refreshNotifications]);

  function handleSelectSession(id) {
    setSessionId(id);
    localStorage.setItem(lastSessionKey, id);
  }

  function handleNewChat() {
    handleSelectSession(newSessionId("mediflow_doctor_session"));
    setMessages([]);
  }

  async function handleAsk(e) {
    e.preventDefault();
    const text = query.trim();
    if (!text || loading) return;
    setMessages((m) => [...m, { role: "user", content: text }]);
    setQuery("");
    setLoading(true);
    try {
      const { reply } = await sendMessage(sessionId, text);
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${err.message}` }]);
    } finally {
      setLoading(false);
      setRefreshKey((k) => k + 1);
    }
  }

  async function handleSummary() {
    setLoading(true);
    setMessages((m) => [...m, { role: "user", content: `Get today's summary for ${doctor.name}` }]);
    try {
      const { reply } = await getDaySummary(sessionId, doctor.name, "today");
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${err.message}` }]);
    } finally {
      setLoading(false);
      setRefreshKey((k) => k + 1);
    }
  }

  return (
    <div className="dashboard">
      <ChatSidebar
        currentSessionId={sessionId}
        onSelect={handleSelectSession}
        onNewChat={handleNewChat}
        onSessionDeleted={handleNewChat}
        refreshKey={refreshKey}
      />
      <div className="panel chat-panel">
        <div className="dashboard-toolbar">
          <span className="doctor-name">{doctor.name}</span>
          <button type="button" onClick={handleSummary} disabled={loading}>
            Get Today's Summary
          </button>
          <div className="notif-wrap">
            <button
              type="button"
              className="notif-bell"
              onClick={() => setNotifOpen((o) => !o)}
              aria-label="Notifications"
            >
              🔔
              {unreadCount > 0 && <span className="notif-badge">{unreadCount}</span>}
            </button>
            {notifOpen && (
              <div className="panel notifications-panel">
                <h3>Notifications</h3>
                {notifications.length === 0 && <p className="muted">No notifications.</p>}
                <ul>
                  {notifications.map((n) => (
                    <li key={n.id} className={n.read ? "read" : "unread"}>
                      <span className="notif-channel">{n.channel}</span>
                      <span>{n.message}</span>
                      {!n.read && (
                        <button
                          type="button"
                          className="mark-read"
                          onClick={async () => {
                            await markNotificationRead(n.id);
                            refreshNotifications();
                          }}
                        >
                          Mark read
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          <button type="button" className="logout" onClick={onLogout}>
            Log out
          </button>
        </div>
        <div className="chat-messages">
          {messages.map((m, i) => (
            <div key={i} className={`chat-bubble ${m.role}`}>
              {m.content}
            </div>
          ))}
          {loading && (
            <div className="chat-bubble assistant thinking">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          )}
        </div>
        <form className="chat-input-row" onSubmit={handleAsk}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. How many patients came in with fever this week?"
            disabled={loading}
          />
          <button type="submit" disabled={loading || !query.trim()}>
            Ask
          </button>
        </form>
      </div>
    </div>
  );
}
