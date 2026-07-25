import { useMemo, useState } from "react";
import PatientChat from "./PatientChat";
import DoctorDashboard from "./DoctorDashboard";
import "./App.css";

function newSessionId(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}

export default function App() {
  const [view, setView] = useState("patient");
  const patientSessionId = useMemo(() => newSessionId("patient"), []);
  const doctorSessionId = useMemo(() => newSessionId("doctor"), []);

  return (
    <div className="app">
      <header className="app-header">
        <h1>MediFlow-AI</h1>
        <nav>
          <button className={view === "patient" ? "active" : ""} onClick={() => setView("patient")}>
            Patient Chat
          </button>
          <button className={view === "doctor" ? "active" : ""} onClick={() => setView("doctor")}>
            Doctor Dashboard
          </button>
        </nav>
      </header>

      <main>
        {view === "patient" ? (
          <PatientChat sessionId={patientSessionId} />
        ) : (
          <DoctorDashboard sessionId={doctorSessionId} />
        )}
      </main>
    </div>
  );
}
