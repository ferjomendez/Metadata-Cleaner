import { useEffect, useState } from "react";
import { api, ApiError, type AppConfig, type Job } from "../api";
import { Notice } from "../components/Notice";
import { StatusBadge } from "../components/StatusBadge";
import { errorMessage, t } from "../i18n";
import { formatDateTime } from "../lib/format";
import { linkHandler } from "../lib/router";

export function History({ config }: { config: AppConfig }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ jobs: Job[] }>("/api/jobs")
      .then((r) => setJobs(r.jobs))
      .catch((err) => setError(errorMessage(err instanceof ApiError ? err.code : null)));
  }, []);

  return (
    <>
      <header className="page-head">
        <h1 id="main-title" tabIndex={-1}>
          {t("history.title")}
        </h1>
        <p className="lede">{t("history.intro", { days: config.history_retention_days })}</p>
      </header>
      {error && <Notice kind="error">{error}</Notice>}
      {jobs === null && !error && <p className="muted">{t("app.loading")}</p>}
      {jobs && jobs.length === 0 && <p className="muted">{t("history.empty")}</p>}
      {jobs && jobs.length > 0 && (
        <ul className="history-list">
          {jobs.map((job) => {
            const href = `/trabajos/${job.id}`;
            return (
              <li key={job.id} className="history-item">
                <div className="row">
                  <strong>{formatDateTime(job.created_at)}</strong>
                  {job.mode && <span className="format-tag">{t(`clean.modes.${job.mode}.name`)}</span>}
                  <StatusBadge status={job.status} label={t(`job.status.${job.status}`)} />
                  <span className="muted small">{t("history.files", { count: job.files.length })}</span>
                  {(job.status === "done" || job.status === "queued" || job.status === "processing") && (
                    <a href={href} onClick={linkHandler(href)} className="btn btn-small">
                      {t("history.open")}
                    </a>
                  )}
                </div>
                <ul className="bullets">
                  {job.files.map((f) => (
                    <li key={f.id}>
                      <span className="file-name">{f.name}</span>{" "}
                      <span className="muted small">
                        {f.format ? (config.formats[f.format]?.label ?? f.format) : ""} ·{" "}
                        {t(`job.fileStatus.${f.status}`)}
                        {(f.error_code || f.reject_code) && ` · ${errorMessage(f.error_code ?? f.reject_code)}`}
                      </span>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
