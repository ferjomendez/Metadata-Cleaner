import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, uploadFile, type AppConfig, type Job, type Mode } from "../api";
import { Dropzone } from "../components/Dropzone";
import { IconAlert, IconX } from "../components/Icons";
import { Notice } from "../components/Notice";
import { StatusBadge } from "../components/StatusBadge";
import { errorMessage, t, warningText } from "../i18n";
import { formatBytes } from "../lib/format";
import { navigate } from "../lib/router";

type ItemStatus = "pending" | "uploading" | "ready" | "rejected" | "error";

interface Item {
  key: string;
  file: File;
  status: ItemStatus;
  progress: number;
  serverId?: string;
  format?: string | null;
  code?: string | null;
  abort?: AbortController;
}

let seq = 0;

export function Clean({ config }: { config: AppConfig }) {
  const [items, setItems] = useState<Item[]>([]);
  const [mode, setMode] = useState<Mode>("conservative");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const jobRef = useRef<Promise<string> | null>(null);
  const pumping = useRef(false);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const { limits } = config;

  const update = (key: string, patch: Partial<Item>) =>
    setItems((list) => list.map((i) => (i.key === key ? { ...i, ...patch } : i)));

  const ensureJob = useCallback(() => {
    if (!jobRef.current) {
      jobRef.current = api.post<Job>("/api/jobs").then((j) => j.id);
      jobRef.current.catch(() => {
        jobRef.current = null;
      });
    }
    return jobRef.current;
  }, []);

  // Upload pending items one at a time.
  const pump = useCallback(async () => {
    if (pumping.current) return;
    pumping.current = true;
    try {
      for (;;) {
        const next = itemsRef.current.find((i) => i.status === "pending");
        if (!next) break;
        const abort = new AbortController();
        update(next.key, { status: "uploading", progress: 0, abort });
        try {
          const jobId = await ensureJob();
          const res = await uploadFile(jobId, next.file, (p) => update(next.key, { progress: p }), abort.signal);
          update(next.key, {
            status: res.status === "ready" ? "ready" : "rejected",
            serverId: res.id,
            format: res.format,
            code: res.reject_code,
            progress: 1,
            abort: undefined,
          });
        } catch (err) {
          update(next.key, { status: "error", code: err instanceof ApiError ? err.code : "generic", abort: undefined });
        }
        // let React flush state before picking the next item
        await new Promise((r) => setTimeout(r, 0));
      }
    } finally {
      pumping.current = false;
    }
  }, [ensureJob]);

  useEffect(() => {
    if (items.some((i) => i.status === "pending")) void pump();
  }, [items, pump]);

  function addFiles(files: File[]) {
    setError(null);
    setItems((list) => {
      const out = [...list];
      let active = out.filter((i) => i.status !== "error" && i.status !== "rejected");
      let total = active.reduce((s, i) => s + i.file.size, 0);
      for (const file of files) {
        let code: string | null = null;
        if (file.size === 0) code = "empty";
        else if (file.size > limits.max_file_bytes) code = "file_too_large";
        else if (active.length >= limits.max_files_per_batch) code = "too_many_files";
        else if (total + file.size > limits.max_batch_bytes) code = "batch_too_large";
        const item: Item = { key: `f${++seq}`, file, status: code ? "error" : "pending", progress: 0, code };
        out.push(item);
        if (!code) {
          active = [...active, item];
          total += file.size;
        }
      }
      return out;
    });
  }

  async function remove(item: Item) {
    item.abort?.abort();
    if (item.serverId && (item.status === "ready" || item.status === "rejected")) {
      try {
        const jobId = await ensureJob();
        await api.del(`/api/jobs/${jobId}/files/${item.serverId}`);
      } catch (err) {
        setError(errorMessage(err instanceof ApiError ? err.code : null));
        return;
      }
    }
    setItems((list) => list.filter((i) => i.key !== item.key));
  }

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const jobId = await ensureJob();
      await api.post(`/api/jobs/${jobId}/start`, { mode });
      navigate(`/trabajos/${jobId}`);
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : null));
      setStarting(false);
    }
  }

  const ready = items.filter((i) => i.status === "ready");
  const uploading = items.some((i) => i.status === "uploading" || i.status === "pending");
  const formats = Array.from(new Set(ready.map((i) => i.format).filter((f): f is string => !!f)));
  const canStart = ready.length > 0 && !uploading && !starting;

  return (
    <>
      <header className="page-head">
        <span className="eyebrow">{t("app.tagline")}</span>
        <h1 id="main-title" tabIndex={-1}>
          {t("clean.title")}
        </h1>
        <p className="lede">{t("clean.intro")}</p>
      </header>

      <div className="grid-2">
        <div className="stack">
          <Dropzone
            disabled={starting}
            maxFiles={limits.max_files_per_batch}
            maxFileBytes={limits.max_file_bytes}
            maxBatchBytes={limits.max_batch_bytes}
            onFiles={addFiles}
          />

          <section className="card" aria-labelledby="files-title">
            <div className="card-head">
              <h2 id="files-title">{t("clean.listTitle")}</h2>
              {items.length > 0 && <span className="muted small">{items.length}</span>}
            </div>
            {items.length === 0 ? (
              <p className="muted">{t("clean.listEmpty")}</p>
            ) : (
              <ul className="file-list">
                {items.map((item) => (
                  <FileRow key={item.key} item={item} config={config} onRemove={() => remove(item)} />
                ))}
              </ul>
            )}
          </section>
        </div>

        <div className="stack">
          <section className="card" aria-labelledby="mode-title">
            <div className="card-head">
              <h2 id="mode-title">{t("clean.modeTitle")}</h2>
            </div>
            <fieldset className="modes">
              <legend className="visually-hidden">{t("clean.modeTitle")}</legend>
              {(["conservative", "deep"] as Mode[]).map((m) => (
                <label key={m} className={`mode-option mode-${m}`}>
                  <input
                    type="radio"
                    name="mode"
                    value={m}
                    checked={mode === m}
                    onChange={() => setMode(m)}
                    aria-describedby={`mode-${m}-desc`}
                  />
                  <span className="mode-name">
                    <span className="mode-label">
                      {m === "deep" && <IconAlert size={16} />}
                      {t(`clean.modes.${m}.name`)}
                    </span>
                    <span className="mode-check" aria-hidden="true">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                        <path d="m5 12 5 5 9-10" />
                      </svg>
                    </span>
                  </span>
                  <p id={`mode-${m}-desc`}>{t(`clean.modes.${m}.summary`)}</p>
                </label>
              ))}
            </fieldset>
          </section>

          <section className={mode === "deep" ? "card card-warn" : "card"} aria-labelledby="warn-title">
            <div className="card-head">
              <h2 id="warn-title">
                {mode === "deep" && <IconAlert size={16} />}
                {t("clean.warningsTitle", { mode: t(`clean.modes.${mode}.name`).toLowerCase() })}
              </h2>
            </div>
            {formats.length === 0 ? (
              <p className="muted">{t("clean.warningsEmpty")}</p>
            ) : (
              <div className="warning-groups">
                {formats.map((f) => {
                  const policy = config.formats[f];
                  if (!policy) return null;
                  return (
                    <div key={f} className="warning-group">
                      <h3>
                        <span className="format-tag">{policy.label}</span>
                      </h3>
                      <ul className="bullets">
                        {policy[mode].warnings.map((w) => (
                          <li key={w}>{warningText(w)}</li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <div className="stack stack-sm">
            {error && (
              <Notice kind="error" live>
                {error}
              </Notice>
            )}
            <button type="button" className="btn btn-primary btn-large" disabled={!canStart} onClick={start}>
              {starting ? t("clean.starting") : t("clean.start")}
            </button>
            {!canStart && !starting && (
              <p className="muted small" aria-live="polite">
                {uploading ? t("clean.startHintUploading") : t("clean.startHintNoFiles")}
              </p>
            )}
            <p className="muted small">{t("clean.retention", { minutes: config.retention_minutes })}</p>
          </div>
        </div>
      </div>
    </>
  );
}

function FileRow({ item, config, onRemove }: { item: Item; config: AppConfig; onRemove: () => void }) {
  const label = item.format ? (config.formats[item.format]?.label ?? item.format) : null;
  const statusLabel =
    item.status === "uploading"
      ? t("clean.fileStatus.uploading", { percent: Math.round(item.progress * 100) })
      : t(`clean.fileStatus.${item.status}`);
  const badgeStatus = item.status === "pending" ? "queued" : item.status;
  return (
    <li className="file-row">
      <div>
        <div className="file-name">{item.file.name}</div>
        <div className="file-meta">
          <span className="num">{formatBytes(item.file.size)}</span>
          {label && <span className="format-tag">{label}</span>}
          <StatusBadge status={badgeStatus} label={statusLabel} />
        </div>
      </div>
      <button
        type="button"
        className="icon-btn"
        onClick={onRemove}
        aria-label={t("clean.remove", { name: item.file.name })}
      >
        <IconX />
      </button>
      {item.status === "uploading" && (
        <progress max={1} value={item.progress} aria-label={statusLabel}>
          {Math.round(item.progress * 100)}%
        </progress>
      )}
      {(item.status === "rejected" || item.status === "error") && (
        <p className="file-error">{errorMessage(item.code)}</p>
      )}
    </li>
  );
}
