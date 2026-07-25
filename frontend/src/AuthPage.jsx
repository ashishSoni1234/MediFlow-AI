import { useEffect, useState } from "react";
import { googleAuth, login, signup } from "./api";
import { supabase } from "./supabaseClient";
import toothLogo from "./assets/tooth-logo.png";

export default function AuthPage({ onAuth }) {
  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [role, setRole] = useState("patient"); // "patient" | "doctor" (signup only)
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);

  // After Supabase redirects back from Google, it hands us a session here.
  // Exchange its access token for our own app JWT via /auth/google.
  useEffect(() => {
    const { data: listener } = supabase.auth.onAuthStateChange(async (event, session) => {
      if (event !== "SIGNED_IN" || !session) return;
      setGoogleLoading(true);
      setError("");
      try {
        const auth = await googleAuth(session.access_token);
        onAuth(auth);
      } catch (err) {
        setError(err.message || "Google sign-in failed");
      } finally {
        setGoogleLoading(false);
        supabase.auth.signOut(); // we only needed Supabase to verify identity, not to keep its own session
      }
    });
    return () => listener.subscription.unsubscribe();
  }, [onAuth]);

  async function handleGoogleSignIn() {
    setError("");
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const auth =
        mode === "login"
          ? await login(email.trim(), password)
          : await signup(name.trim(), email.trim(), password, role);
      onAuth(auth);
    } catch (err) {
      setError(err.message || `${mode} failed`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-blob auth-blob-1" aria-hidden="true" />
      <div className="auth-blob auth-blob-2" aria-hidden="true" />
      <div className="auth-blob auth-blob-3" aria-hidden="true" />
      <div className="auth-grid" aria-hidden="true" />

      <div className="auth-card">
        <div className="auth-card-brand">
          <img src={toothLogo} alt="" aria-hidden="true" />
          <span>MediFlow</span>
        </div>

        <h2 className="auth-title">{mode === "login" ? "Welcome back" : "Create your account"}</h2>
        <p className="auth-subtitle">
          {mode === "login"
            ? "Log in to manage appointments and chat with your clinic."
            : "Join MediFlow to book visits and stay in sync with your care team."}
        </p>

        <div className="mode-toggle">
          <button type="button" className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
            Log in
          </button>
          <button type="button" className={mode === "signup" ? "active" : ""} onClick={() => setMode("signup")}>
            Sign up
          </button>
          <span className={`mode-toggle-thumb ${mode}`} aria-hidden="true" />
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          {mode === "signup" && (
            <>
              <div className="role-toggle">
                <label className={role === "patient" ? "active" : ""}>
                  <input
                    type="radio"
                    name="role"
                    value="patient"
                    checked={role === "patient"}
                    onChange={() => setRole("patient")}
                  />
                  Patient
                </label>
                <label className={role === "doctor" ? "active" : ""}>
                  <input
                    type="radio"
                    name="role"
                    value="doctor"
                    checked={role === "doctor"}
                    onChange={() => setRole("doctor")}
                  />
                  Doctor
                </label>
              </div>
              <div className="input-wrap">
                <input
                  type="text"
                  placeholder="Full name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </div>
            </>
          )}
          <div className="input-wrap">
            <input
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="input-wrap">
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && <p className="login-error">{error}</p>}
          <button type="submit" className="auth-submit" disabled={loading}>
            {loading ? "Please wait…" : mode === "login" ? "Log in" : "Sign up"}
          </button>
        </form>

        <div className="auth-divider">or continue with</div>

        <button
          type="button"
          className="google-signin-btn"
          onClick={handleGoogleSignIn}
          disabled={googleLoading}
        >
          {googleLoading ? "Signing in…" : "Sign in with Google"}
        </button>
      </div>
    </div>
  );
}
