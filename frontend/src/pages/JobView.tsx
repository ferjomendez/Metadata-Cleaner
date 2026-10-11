import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AppConfig, type Job, type JobFile } from "../api";
import { Countdown } from "../components/Countdown";
import { IconDownload, IconTrash } from "../components/Icons";
import { Notice } from "../components/Notice";
import { ReportPanel } from "../components/ReportPanel";
import { StatusBadge } from "../components/StatusBadge";
import { errorMessage, t } from "../i18n";
import { clockSkew, formatBytes } from "../lib/format";
import { linkHandler } from "../lib/router";

const ACTIVE = new Set(["queued", "processing"]);
const FINISHED = new Set(["done", "failed", "cancelled", "expired"]);

export function JobView({ id, config }: { id: string; config: AppConfig }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  const load = useCallback(async () => {
    try {
      setJob(await api.get<Job>(`/api/jobs/${id}`));
      setError(null);
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : null));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while the job is queued or processing.
  useEffect(() => {
    if (!job || !ACTIVE.has(job.status)) return;
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [job, load]);

  async function deleteNow() {
    if (!window.confirm(t("job.deleteConfirm"))) return;
    try {
      await api.del(`/api/jobs/${id}`);
      await load();
    } catch (err) {
      setError(errorMessage(err instanceof ApiError ? err.code : null));
    }
  }

  if (!job) {
    return (
      <>
        <h1 id="main-title" tabIndex={-1} className="visually-hidden">
          {t("job.title")}
        </h1>
        {error ? <Notice kind="error">{error}</Notice> : <p className="muted">{t("app.loading")}</p>}
      </>
    );
  }

  const files = job.files.filter((f) => f.status !== "rejected");
  const processed = files.filter((f) => FINISHED.has(f.status)).length;
  const skew = clockSkew(job.server_time);
  const downloadable = job.status === "done" && !expired && job.files.some((f) => f.downloadable);
  const progressText = t("job.progress", { done: processed, total: files.length });

  return (
    <>
      <header className="page-head">
        <span className="eyebrow">{job.mode ? t("job.mode", { mode: t(`clean.modes.${job.mode}.name`) }) : ""}</span>
        <h1 id="main-title" tabIndex={-1}>
          {t("job.title")}
        </h1>
        <p>
          <a href="/" onClick={linkHandler("/")}>
            {t("job.back")}
          </a>
        </p>
      </header>

      <div className="stack">
        <section className="card" aria-labelledby="summary-title">
          <h2 id="summary-title" className="visually-hidden">
            {t("job.title")}
          </h2>
          <div className="job-summary">
            <div className="stack stack-xs">
              <StatusBadge status={job.status} label={t(`job.status.${job.status}`)} />
              <span aria-live="polite" className="muted small">
                {progressText}
              </span>
            </div>
            {downloadable && <Countdown expiresAt={job.expires_at} skewMs={skew} onExpire={() => setExpired(true)} />}
          </div>
          <progress
            max={Math.max(files.length, 1)}
            value={processed}
            aria-label={progressText}
            className="job-progress"
          />
          {downloadable && (
            <div className="result-actions job-actions">
              <a className="btn btn-primary" href={`/api/jobs/${id}/download.zip`} download>
                <IconDownload /> {t("job.downloadAll")}
              </a>
              <button type="button" className="btn btn-danger" onClick={deleteNow}>
                <IconTrash /> {t("job.deleteNow")}
              </button>
            </div>
          )}
          {error && <Notice kind="error">{error}</Notice>}
          {job.deletion_pending && <Notice kind="warn">{t("job.deletionPending")}</Notice>}
          {(job.status === "expired" || job.status === "deleted" || expired) && (
            <Notice kind="info">{t("job.countdownExpired")}</Notice>
          )}
          {job.status === "done" && !expired && <p className="muted small job-note">{t("job.retentionNotice")}</p>}
        </section>

        <Notice kind="warn">{t("job.notAnonymous")}</Notice>

        <section aria-labelledby="files-title" className="stack stack-sm">
          <h2 id="files-title">{t("clean.listTitle")}</h2>
          {job.files.map((f) => (
            <ResultCard key={f.id} jobId={id} file={f} config={config} allowDownload={downloadable} />
          ))}
        </section>
      </div>
    </>
  );
}

function ResultCard({
  jobId,
  file,
  config,
  allowDownload,
}: {
  jobId: string;
  file: JobFile;
  config: AppConfig;
  allowDownload: boolean;
}) {
  const [open, setOpen] = useState(false);
  const label = file.format ? (config.formats[file.format]?.label ?? file.format) : null;
  const s = file.summary;
  const panelId = `report-${file.id}`;
  const code = file.error_code ?? file.reject_code;
  return (
    <article className="result" aria-labelledby={`name-${file.id}`}>
      <div className="result-head">
        <div className="stack stack-xs">
          <div className="file-name" id={`name-${file.id}`}>
            {file.name}
          </div>
          <div className="file-meta">
            {label && <span className="format-tag">{label}</span>}
            <StatusBadge status={file.status} />
            {s && (
              <span>
                {t("job.sizeChange", { before: formatBytes(s.size_before), after: formatBytes(s.size_after) })}
              </span>
            )}
          </div>
          {s && (
            <div className="counts">
              <span className="count-chip">
                {t("job.removed")} <strong>{s.removed.length}</strong>
              </span>
              <span className="count-chip">
                {t("job.retained")} <strong>{s.retained.length}</strong>
              </span>
              <span className="count-chip">
                {t("job.notVerified")} <strong>{s.not_verified.length}</strong>
              </span>
            </div>
          )}
          {(file.status === "failed" || file.status === "rejected") && <p className="file-error">{errorMessage(code)}</p>}
        </div>
        <div className="result-actions">
          {allowDownload && file.downloadable && (
            <a
              className="btn btn-small btn-primary"
              href={`/api/jobs/${jobId}/files/${file.id}/download`}
              download
              aria-label={t("job.downloadNamed", { name: file.output_name ?? file.name })}
            >
              <IconDownload /> {t("job.download")}
            </a>
          )}
          {(s || file.downloadable) && (
            <button
              type="button"
              className="btn btn-small"
              aria-expanded={open}
              aria-controls={panelId}
              onClick={() => setOpen((v) => !v)}
            >
              {open ? t("job.hideDetails") : t("job.details")}
            </button>
          )}
        </div>
      </div>
      {open && (
        <div id={panelId}>
          <ReportPanel jobId={jobId} file={file} />
        </div>
      )}
    </article>
  );
}
