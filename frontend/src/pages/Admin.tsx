import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type User } from "../api";
import { Notice } from "../components/Notice";
import { errorMessage, t } from "../i18n";
import { formatBytes, formatDateTime } from "../lib/format";

interface AdminUser {
  id: number;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  locked: boolean;
  failed_logins: number;
  active_sessions: number;
}

interface Health {
  server_time: string;
  storage: {
    quota_bytes: number;
    accounted_bytes: number;
    measured_bytes: number;
    free_disk_bytes: number;
    min_free_disk_bytes: number;
  };
  queue: { queued: number; processing: number };
  worker: null | {
    heartbeat_age_seconds: number;
    healthy: boolean;
    tools: Record<string, string>;
    last_cleanup_at: string | null;
  };
  last_cleanup: null | { at: string; by: string; result: Record<string, number> };
  deletion_failures: { path: string; attempts: number; error: string; since: string }[];
}

export function Admin({ me }: { me: User }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, h] = await Promise.all([
        api.get<{ users: AdminUser[] }>("/api/admin/users"),
        api.get<Health>("/api/admin/health"),
      ]);
      setUsers(u.users);
      setHealth(h);
    } catch (err) {
      setMsg({ kind: "error", text: errorMessage(err instanceof ApiError ? err.code : null) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<unknown>, okText = t("admin.done")): Promise<boolean> {
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: "ok", text: okText });
      await load();
      return true;
    } catch (err) {
      setMsg({ kind: "error", text: errorMessage(err instanceof ApiError ? err.code : null) });
      return false;
    }
  }

  return (
    <>
      <header className="page-head">
        <h1 id="main-title" tabIndex={-1}>
          {t("admin.title")}
        </h1>
      </header>
      <div className="stack">
        {msg && (
          <Notice kind={msg.kind} live>
            {msg.text}
          </Notice>
        )}
        <section className="card" aria-labelledby="health-title">
          <div className="card-head">
            <h2 id="health-title">{t("admin.health")}</h2>
            <button type="button" className="btn btn-small" onClick={() => void load()}>
              {t("admin.refresh")}
            </button>
          </div>
          {health ? <HealthView h={health} /> : <p className="muted">{t("app.loading")}</p>}
        </section>

        <section className="card" aria-labelledby="users-title">
          <div className="card-head">
            <h2 id="users-title">{t("admin.users")}</h2>
          </div>
          <CreateUser
            onCreated={(name) => {
              setMsg({ kind: "ok", text: t("admin.created", { name }) });
              void load();
            }}
          />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">{t("admin.columns.user")}</th>
                  <th scope="col">{t("admin.columns.role")}</th>
                  <th scope="col">{t("admin.columns.state")}</th>
                  <th scope="col">{t("admin.columns.sessions")}</th>
                  <th scope="col">{t("admin.columns.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <UserRow key={u.id} u={u} self={u.id === me.id} act={act} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </>
  );
}

function HealthView({ h }: { h: Health }) {
  const s = h.storage;
  const ratio = s.quota_bytes ? s.accounted_bytes / s.quota_bytes : 0;
  const level = ratio > 0.9 ? "danger" : ratio > 0.7 ? "warn" : "ok";
  const diskLow = s.free_disk_bytes < s.min_free_disk_bytes;
  const w = h.worker;
  return (
    <div className="stack">
      <div className="stats">
        <div className="stat">
          <span className="label">{t("admin.quota")}</span>
          <span className="value">
            {t("admin.quotaValue", { used: formatBytes(s.accounted_bytes), total: formatBytes(s.quota_bytes) })}
          </span>
          <div
            className="meter"
            data-level={level}
            role="meter"
            aria-label={t("admin.quota")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(ratio * 100)}
          >
            <span className={`fill-${Math.min(100, Math.round(ratio * 20) * 5)}`} />
          </div>
          <span className="muted small">{t("admin.measured", { value: formatBytes(s.measured_bytes) })}</span>
        </div>
        <div className="stat">
          <span className="label">{t("admin.freeDisk")}</span>
          <span className="value">{formatBytes(s.free_disk_bytes)}</span>
          <span className={diskLow ? "small file-error" : "muted small"}>
            {t("admin.freeDiskFloor", { value: formatBytes(s.min_free_disk_bytes) })}
          </span>
        </div>
        <div className="stat">
          <span className="label">{t("admin.queue")}</span>
          <span className="value">
            {t("admin.queueValue", { queued: h.queue.queued, processing: h.queue.processing })}
          </span>
        </div>
        <div className="stat">
          <span className="label">{t("admin.worker")}</span>
          <span className={w && w.healthy ? "value" : "value file-error"}>
            {!w
              ? t("admin.workerMissing")
              : w.healthy
                ? t("admin.workerOk", { seconds: Math.round(w.heartbeat_age_seconds) })
                : t("admin.workerStale", { seconds: Math.round(w.heartbeat_age_seconds) })}
          </span>
        </div>
        <div className="stat">
          <span className="label">{t("admin.lastCleanup")}</span>
          <span className="value">{h.last_cleanup ? formatDateTime(h.last_cleanup.at) : t("admin.never")}</span>
          {h.last_cleanup && (
            <span className="muted small mono">
              {Object.entries(h.last_cleanup.result)
                .filter(([, v]) => v)
                .map(([k, v]) => `${k}=${v}`)
                .join(" ") || "–"}
            </span>
          )}
        </div>
        <div className="stat">
          <span className="label">{t("admin.deletionFailures")}</span>
          <span className={h.deletion_failures.length ? "value file-error" : "value"}>
            {h.deletion_failures.length || t("admin.noFailures")}
          </span>
          {h.deletion_failures.slice(0, 5).map((f) => (
            <span key={f.path} className="muted small mono">
              {f.path} ({f.attempts}× {f.error})
            </span>
          ))}
        </div>
      </div>
      {w && (
        <div>
          <h3>{t("admin.tools")}</h3>
          <ul className="kv">
            {Object.entries(w.tools).map(([k, v]) => (
              <li key={k}>
                <span className="k">{k}</span>
                <span className="v">{v}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function CreateUser({ onCreated }: { onCreated: (name: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/api/admin/users", { username, password, is_admin: isAdmin });
      onCreated(username);
      setUsername("");
      setPassword("");
      setIsAdmin(false);
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : null));
    }
  }

  return (
    <form className="stack create-user" onSubmit={submit} aria-labelledby="create-title">
      <h3 id="create-title">{t("admin.createTitle")}</h3>
      {error && (
        <Notice kind="error" live>
          {error}
        </Notice>
      )}
      <div className="form-row">
        <div className="field">
          <label htmlFor="nu-name">{t("admin.username")}</label>
          <input id="nu-name" className="input" autoComplete="off" autoCapitalize="none" spellCheck={false}
            value={username} onChange={(e) => setUsername(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="nu-pass">{t("admin.password")}</label>
          <input id="nu-pass" className="input" type="password" autoComplete="new-password" minLength={12}
            value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
          {t("admin.isAdmin")}
        </label>
        <button className="btn btn-primary" type="submit" disabled={!username || !password}>
          {t("admin.create")}
        </button>
      </div>
    </form>
  );
}

function UserRow({ u, self, act }: { u: AdminUser; self: boolean; act: (fn: () => Promise<unknown>) => Promise<boolean> }) {
  const [resetting, setResetting] = useState(false);
  const [pw, setPw] = useState("");
  const base = `/api/admin/users/${u.id}`;
  return (
    <tr>
      <th scope="row">{u.username}</th>
      <td>{u.is_admin ? t("admin.roleAdmin") : t("admin.roleUser")}</td>
      <td>
        {u.is_active ? t("admin.active") : t("admin.disabled")}
        {u.locked && <span className="badge badge-warn">{t("admin.locked")}</span>}
      </td>
      <td>{u.active_sessions}</td>
      <td>
        <div className="actions-cell">
          {!self && (
            <button
              type="button"
              className="btn btn-small"
              onClick={() => {
                if (u.is_active && !window.confirm(t("admin.confirmDisable", { name: u.username }))) return;
                act(() => api.patch(base, { is_active: !u.is_active }));
              }}
            >
              {u.is_active ? t("admin.disable") : t("admin.enable")}
            </button>
          )}
          {!self && (
            <button type="button" className="btn btn-small" onClick={() => act(() => api.patch(base, { is_admin: !u.is_admin }))}>
              {u.is_admin ? t("admin.removeAdmin") : t("admin.makeAdmin")}
            </button>
          )}
          <button type="button" className="btn btn-small" aria-expanded={resetting} onClick={() => setResetting((v) => !v)}>
            {t("admin.resetPassword")}
          </button>
          <button type="button" className="btn btn-small" onClick={() => act(() => api.post(`${base}/revoke-sessions`))}>
            {t("admin.revokeSessions")}
          </button>
          {u.locked && (
            <button type="button" className="btn btn-small" onClick={() => act(() => api.post(`${base}/unlock`))}>
              {t("admin.unlock")}
            </button>
          )}
        </div>
        {resetting && (
          <form
            className="form-row reset-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act(() => api.post(`${base}/password`, { password: pw })).then((ok) => {
                if (ok) {
                  setPw("");
                  setResetting(false);
                }
              });
            }}
          >
            <div className="field">
              <label htmlFor={`pw-${u.id}`}>{t("admin.newPassword", { name: u.username })}</label>
              <input id={`pw-${u.id}`} className="input" type="password" autoComplete="new-password" minLength={12}
                value={pw} onChange={(e) => setPw(e.target.value)} required />
            </div>
            <button className="btn btn-small btn-primary" type="submit" disabled={!pw}>
              {t("common.save")}
            </button>
          </form>
        )}
      </td>
    </tr>
  );
}
