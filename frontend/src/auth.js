const TOKEN_KEY = "mediflow_token";

function decodeJwtPayload(token) {
  const base64url = token.split(".")[1];
  const base64 = base64url.replace(/-/g, "+").replace(/_/g, "/");
  const json = decodeURIComponent(
    atob(base64)
      .split("")
      .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
      .join("")
  );
  return JSON.parse(json);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
}

export function getCurrentUser() {
  const token = getToken();
  if (!token) return null;
  try {
    const claims = decodeJwtPayload(token);
    if (claims.exp && Date.now() / 1000 > claims.exp) {
      logout();
      return null;
    }
    return { role: claims.role, name: claims.name, linkedId: claims.linked_id };
  } catch {
    logout();
    return null;
  }
}
