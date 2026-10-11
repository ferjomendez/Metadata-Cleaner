import { useEffect, useState } from "react";
import { api, type JobFile, type Report, type ReportItem } from "../api";
import { checkText, findingText, notVerifiedText, t, warningText } from "../i18n";
import { splitKey } from "../lib/format";
import { IconCheck, IconX } from "./Icons";

function Key({ k }: { k: string }) {
  const { group, tag } = splitKey(k);
  return (
    <span className="k">
      {group && <span className="g">{group}:</span>}
      {tag}
    </span>
  );
}

function ItemList({ items, redact, empty }: { items: ReportItem[]; redact?: boolean; empty: string }) {
  if (!items.length) return <p className="muted small">{empty}</p>;
  return (
    <ul className="kv">
      {items.map((i, n) => (
        <li key={`${i.key}-${n}`}>
          <Key k={i.key} />
          {i.value && (
            <span className="v">
              {redact ? (
                <span className="redacted" title={t("job.valueHidden")}>
                  {i.value}
                </span>
              ) : (
                i.value
              )}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Before/after detail for one file. Loads the full report (with values) on
 * demand; the database summary only holds tag names. */
export function ReportPanel({ jobId, file }: { jobId: string; file: JobFile }) {
  const [report, setReport] = useState<Report | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "missing">("loading");
  const [reveal, setReveal] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .get<Report>(`/api/jobs/${jobId}/files/${file.id}/report`)
      .then((r) => {
        if (alive) {
          setReport(r);
          setState("ok");
        }
      })
      .catch(() => alive && setState("missing"));
    return () => {
      alive = false;
    };
  }, [jobId, file.id]);

  const s = file.summary;
  if (state === "loading") return <p className="muted small">{t("job.loadingReport")}</p>;

  // Fall back to the stored summary (names only) once the report file is gone.
  const removed: ReportItem[] = report ? report.removed : (s?.removed ?? []).map((key) => ({ key, value: "" }));
  const retained: ReportItem[] = report ? report.retained : (s?.retained ?? []).map((key) => ({ key, value: "" }));
  const notVerified = report?.not_verified ?? s?.not_verified ?? [];
  const checks = report?.checks ?? s?.checks ?? [];
  const warnings = report?.warnings ?? s?.warnings ?? [];
  const findings = report?.findings ?? (s?.findings ?? []).map((code) => ({ code, value: "" }));

  return (
    <div className={reveal ? "report reveal" : "report"}>
      {state === "missing" && <p className="muted small">{t("job.reportUnavailable")}</p>}
      <div className="report-cols">
        <section aria-label={t("job.removed")}>
          <h4>
            {t("job.removed")} ({removed.length})
          </h4>
          <ItemList items={removed} redact empty={t("job.noneRemoved")} />
          {report && removed.some((i) => i.value) && (
            <p className="small reveal-toggle">
              <button type="button" className="link-btn" onClick={() => setReveal((v) => !v)} aria-pressed={reveal}>
                {reveal ? t("job.hideValues") : t("job.revealValues")}
              </button>
            </p>
          )}
        </section>
        <section aria-label={t("job.retained")}>
          <h4>
            {t("job.retained")} ({retained.length})
          </h4>
          <ItemList items={retained} empty={t("job.noneRetained")} />
        </section>
        <section aria-label={t("job.notVerified")}>
          <h4>
            {t("job.notVerified")} ({notVerified.length})
          </h4>
          <ul className="bullets">
            {notVerified.map((c) => (
              <li key={c}>{notVerifiedText(c)}</li>
            ))}
          </ul>
        </section>
      </div>
      {warnings.length > 0 && (
        <section>
          <h4>{t("job.warnings")}</h4>
          <ul className="bullets">
            {warnings.map((w) => (
              <li key={w}>{warningText(w)}</li>
            ))}
          </ul>
        </section>
      )}
      {findings.length > 0 && (
        <section>
          <h4>{t("job.findings")}</h4>
          <ul className="kv">
            {findings.map((f, i) => (
              <li key={`${f.code}-${i}`}>
                <span className="k">{findingText(f.code)}</span>
                {f.value && <span className="v">{f.value}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {checks.length > 0 && (
        <section>
          <h4>{t("job.checks")}</h4>
          <ul className="check-list">
            {checks.map((c) => (
              <li key={c.code}>
                <span className={c.ok ? "ok" : "bad"}>{c.ok ? <IconCheck /> : <IconX />}</span>
                <span>
                  {checkText(c.code)}
                  <span className="visually-hidden">: {c.ok ? t("common.yes") : t("common.no")}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
