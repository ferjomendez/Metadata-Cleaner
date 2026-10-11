import { useState, type FormEvent } from "react";
import { api, ApiError, setCsrf, type Session } from "../api";
import { Notice } from "../components/Notice";
import { errorMessage, t } from "../i18n";

export function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const s = await api.post<Session>("/api/auth/login", { username, password });
      setCsrf(s.csrf);
      setPassword("");
      onLogin(s);
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : null));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="card login-card">
        <div className="page-head tight">
          <span className="eyebrow">{t("app.tagline")}</span>
          <h1 id="main-title" tabIndex={-1}>
            {t("login.title")}
          </h1>
          <p className="muted">{t("login.intro")}</p>
        </div>
        <form onSubmit={submit} noValidate>
          {error && (
            <Notice kind="error" live>
              {error}
            </Notice>
          )}
          <div className="field">
            <label htmlFor="username">{t("login.username")}</label>
            <input
              id="username"
              className="input"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="password">{t("login.password")}</label>
            <input
              id="password"
              className="input"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <button className="btn btn-primary btn-large" type="submit" disabled={busy || !username || !password}>
            {busy ? t("login.submitting") : t("login.submit")}
          </button>
        </form>
      </div>
    </div>
  );
}
