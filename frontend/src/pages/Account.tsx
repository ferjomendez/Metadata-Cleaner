import { useState, type FormEvent } from "react";
import { api, ApiError, type User } from "../api";
import { Notice } from "../components/Notice";
import { errorMessage, t } from "../i18n";

export function Account({ user }: { user: User }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (next !== confirm) {
      setMsg({ kind: "error", text: t("account.mismatch") });
      return;
    }
    setBusy(true);
    try {
      await api.post("/api/auth/password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setConfirm("");
      setMsg({ kind: "ok", text: t("account.saved") });
    } catch (err) {
      setMsg({ kind: "error", text: errorMessage(err instanceof ApiError ? err.code : null) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="page-head">
        <span className="eyebrow">{user.username}</span>
        <h1 id="main-title" tabIndex={-1}>
          {t("account.title")}
        </h1>
      </header>
      <section className="card narrow" aria-labelledby="pw-title">
        <h2 id="pw-title" className="card-title">
          {t("account.changePassword")}
        </h2>
        <form className="stack" onSubmit={submit}>
          {msg && (
            <Notice kind={msg.kind} live>
              {msg.text}
            </Notice>
          )}
          <input type="text" name="username" autoComplete="username" value={user.username} readOnly hidden />
          <div className="field">
            <label htmlFor="pw-current">{t("account.current")}</label>
            <input id="pw-current" className="input" type="password" autoComplete="current-password"
              value={current} onChange={(e) => setCurrent(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="pw-next">{t("account.next")}</label>
            <input id="pw-next" className="input" type="password" autoComplete="new-password" minLength={12}
              aria-describedby="pw-hint" value={next} onChange={(e) => setNext(e.target.value)} required />
            <span id="pw-hint" className="muted small">{t("account.hint")}</span>
          </div>
          <div className="field">
            <label htmlFor="pw-confirm">{t("account.confirm")}</label>
            <input id="pw-confirm" className="input" type="password" autoComplete="new-password"
              value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
          </div>
          <div>
            <button className="btn btn-primary" type="submit" disabled={busy || !current || !next || !confirm}>
              {t("account.save")}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}
