import { t } from "../i18n";
import { IconCheck, IconX } from "./Icons";

const tone: Record<string, string> = {
  done: "ok",
  ready: "ok",
  failed: "error",
  rejected: "error",
  error: "error",
  queued: "busy",
  processing: "busy",
  uploading: "busy",
  expired: "",
  cancelled: "",
  deleted: "",
  draft: "",
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const kind = tone[status] ?? "";
  const cls = kind === "busy" ? "badge badge-busy" : kind ? `badge badge-${kind}` : "badge";
  return (
    <span className={cls}>
      {kind === "ok" && <IconCheck size={12} />}
      {kind === "error" && <IconX size={12} />}
      {label ?? t(`job.fileStatus.${status}`)}
    </span>
  );
}
