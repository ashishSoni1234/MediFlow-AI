const AGENT_URL = import.meta.env.VITE_AGENT_SERVICE_URL || "http://127.0.0.1:8200";

export async function sendMessage(sessionId, text, role, userRef) {
  const res = await fetch(`${AGENT_URL}/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text, role, user_ref: userRef ?? null }),
  });
  if (!res.ok) throw new Error(`agent/message failed: ${res.status}`);
  return res.json();
}

export async function getDaySummary(sessionId, doctorName, date, userRef) {
  const res = await fetch(`${AGENT_URL}/agent/summary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, doctor_name: doctorName, date, user_ref: userRef ?? null }),
  });
  if (!res.ok) throw new Error(`agent/summary failed: ${res.status}`);
  return res.json();
}

export async function getNotifications(doctorId) {
  const res = await fetch(`${AGENT_URL}/notifications/${doctorId}`);
  if (!res.ok) throw new Error(`notifications fetch failed: ${res.status}`);
  return res.json();
}

export async function markNotificationRead(notificationId) {
  await fetch(`${AGENT_URL}/notifications/${notificationId}/read`, { method: "POST" });
}
