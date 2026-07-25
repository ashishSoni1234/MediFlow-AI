export function getOrCreateSessionId(key) {
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const id = `${key}-${crypto.randomUUID()}`;
  localStorage.setItem(key, id);
  return id;
}

export function newSessionId(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}
