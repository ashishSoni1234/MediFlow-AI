import { getToken, logout } from "./auth";

const AGENT_URL = import.meta.env.VITE_AGENT_SERVICE_URL || "http://127.0.0.1:8200";

async function authFetch(path, options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${AGENT_URL}${path}`, { ...options, headers });
  if (res.status === 401) {
    logout();
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${path} failed: ${res.status}`);
  }
  return res.json();
}

export async function signup(name, email, password, role) {
  const res = await fetch(`${AGENT_URL}/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, email, password, role }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `signup failed: ${res.status}`);
  }
  return res.json();
}

export async function login(email, password) {
  const res = await fetch(`${AGENT_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `login failed: ${res.status}`);
  }
  return res.json();
}

export async function googleAuth(accessToken) {
  const res = await fetch(`${AGENT_URL}/auth/google`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: accessToken }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `google auth failed: ${res.status}`);
  }
  return res.json();
}

export async function sendMessage(sessionId, text) {
  return authFetch(`/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text }),
  });
}

export async function getDaySummary(sessionId, doctorName, date) {
  return authFetch(`/agent/summary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, doctor_name: doctorName, date }),
  });
}

export async function getNotifications(doctorId) {
  return authFetch(`/notifications/${doctorId}`);
}

export async function markNotificationRead(notificationId) {
  return authFetch(`/notifications/${notificationId}/read`, { method: "POST" });
}

export async function getSessionHistory(sessionId) {
  return authFetch(`/session/${sessionId}/history`);
}

export async function getSessions() {
  return authFetch(`/sessions`);
}

export async function renameSession(sessionId, title) {
  return authFetch(`/session/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function deleteSession(sessionId) {
  return authFetch(`/session/${sessionId}`, { method: "DELETE" });
}

export async function deleteAllSessions() {
  return authFetch(`/sessions`, { method: "DELETE" });
}
