import { useState, useEffect, useCallback } from "react";
import { sendMessage, getDaySummary, getNotifications, markNotificationRead } from "./api";

// Seeded demo doctors (see db/seed.sql) — just for the dashboard's doctor
// picker, unrelated to how the LLM chooses tools.
const DOCTORS = [
  { id: 1, name: "Dr. Ahuja" },
  { id: 2, name: "Dr. Mehta" },
  { id: 3, name: "Dr. Rao" },
];

export default function DoctorDashboard({ sessionId }) {
  const [doctor, setDoctor] = useState(DOCTORS[0]);
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [notifications, setNotifications] = useState([]);

  const refreshNotifications = useCallback(async () => {
    try {
      const list = await getNotifications(doctor.id);
      setNotifications(list);
    } catch {
      // polling best-effort; ignore transient errors
    }
  }, [doctor.id]);

  useEffect(() => {
    refreshNotifications();
    const interval = setInterval(refreshNotifications, 8000);
    return () => clearInterval(interval);
  }, [refreshNotifications]);

  async function handleAsk(e) {
    e.preventDefault();
    const text = query.trim();
    if (!text || loading) return;
    setMessages((m) => [...m, { role: "user", content: text }]);
    setQuery("");
    setLoading(true);
    try {
      const { reply } = await sendMessage(sessionId, text, "doctor", doctor.id);
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  }

  async function handleSummary() {
    setLoading(true);
    setMessages((m) => [...m, { role: "user", content: `Get today's summary for ${doctor.name}` }]);
    try {
      const { reply } = await getDaySummary(sessionId, doctor.name, "today", doctor.id);
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="dashboard">
      <div className="panel chat-panel">
        <div className="dashboard-toolbar">
          <select value={doctor.id} onChange={(e) => setDoctor(DOCTORS.find((d) => d.id === Number(e.target.value)))}>
            {DOCTORS.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <button type="button" onClick={handleSummary} disabled={loading}>
            Get Today's Summary
          </button>
        </div>
        <div className="chat-messages">
          {messages.map((m, i) => (
            <div key={i} className={`chat-bubble ${m.role}`}>
              {m.content}
            </div>
          ))}
          {loading && <div className="chat-bubble assistant thinking">…</div>}
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
    </div>
  );
}
