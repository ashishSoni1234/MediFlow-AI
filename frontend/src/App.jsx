import { useState } from "react";
import PatientChat from "./PatientChat";
import DoctorDashboard from "./DoctorDashboard";
import AuthPage from "./AuthPage";
import PromptHistory from "./PromptHistory";
import { getCurrentUser, setToken, logout as clearAuth } from "./auth";
import toothLogo from "./assets/tooth-logo.png";
import "./App.css";

export default function App() {
  const [user, setUser] = useState(getCurrentUser);
  const [tab, setTab] = useState("main"); // "main" | "history"

  function handleAuth(auth) {
    setToken(auth.token);
    setUser({ role: auth.role, name: auth.name, linkedId: auth.linked_id });
    setTab("main");
  }

  function handleLogout() {
    clearAuth();
    setUser(null);
    setTab("main");
  }
// this will return all  div 
  return (
    <div className={`app${!user ? " app--auth" : ""}`}>
      {user && (
        <div className="app-bg" aria-hidden="true">
          <div className="auth-blob auth-blob-1" />
          <div className="auth-blob auth-blob-2" />
          <div className="auth-blob auth-blob-3" />
          <div className="auth-grid" />
        </div>
      )}
      {user && (
        <header className="app-header">
          <h1 className="brand">
            <img className="brand-icon" src={toothLogo} alt="" aria-hidden="true" />
            MediFlow
          </h1>
          <nav>
            <button className={tab === "main" ? "active" : ""} onClick={() => setTab("main")}>
              {user.role === "doctor" ? "Dashboard" : "Chat"}
            </button>
            <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>
              History
            </button>
            <button className="logout" onClick={handleLogout}>
              Log out
            </button>
          </nav>
        </header>
      )}

      <main className={!user ? "auth-main" : ""}>
        {!user ? (
          <AuthPage onAuth={handleAuth} />
        ) : tab === "history" ? (
          <PromptHistory />
        ) : user.role === "doctor" ? (
          <DoctorDashboard doctor={user} onLogout={handleLogout} />
        ) : (
          <PatientChat patient={user} />
        )}
      </main>
    </div>
  );
}
