import { useState, useRef, useEffect } from "react";
import { sendMessage, getSessionHistory } from "./api";
import ChatSidebar from "./ChatSidebar";
import { getOrCreateSessionId, newSessionId } from "./session";

const LAST_SESSION_KEY_PREFIX = "mediflow_patient_last_session_";

const WELCOME = {
  role: "assistant",
  content: "Hi! I can help you check doctor availability or book an appointment. How can I help?",
};

// `patient` is the identity established via JWT at login (see AuthPage.jsx)
// — scoping the remembered session id by linkedId keeps two patients on the
// same browser from reusing (and getting rejected from) each other's session.
export default function PatientChat({ patient }) {
  const lastSessionKey = `${LAST_SESSION_KEY_PREFIX}${patient.linkedId}`;
  const [sessionId, setSessionId] = useState(() => getOrCreateSessionId(lastSessionKey));
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const bottomRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    getSessionHistory(sessionId)
      .then((history) => {
        if (!cancelled) setMessages(history.length > 0 ? history : [WELCOME]);
      })
      .catch(() => {
        // no prior session yet, or agent service unreachable — keep the welcome message
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  function handleSelectSession(id) {
    setSessionId(id);
    localStorage.setItem(lastSessionKey, id);
  }

  function handleNewChat() {
    handleSelectSession(newSessionId("mediflow_patient_session"));
    setMessages([WELCOME]);
  }

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
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

  return (
    <div className="chat-layout">
      <ChatSidebar
        currentSessionId={sessionId}
        onSelect={handleSelectSession}
        onNewChat={handleNewChat}
        onSessionDeleted={handleNewChat}
        refreshKey={refreshKey}
      />
      <div className="panel chat-panel">
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
          <div ref={bottomRef} />
        </div>
        <form className="chat-input-row" onSubmit={handleSend}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g. Book me with Dr. Ahuja tomorrow at 3pm"
            disabled={loading}
          />
          <button type="submit" disabled={loading || !input.trim()}>
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
