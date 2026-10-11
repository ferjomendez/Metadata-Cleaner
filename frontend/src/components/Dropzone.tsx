import { useRef, useState } from "react";
import { t } from "../i18n";
import { formatBytes } from "../lib/format";
import { IconUpload } from "./Icons";

interface Props {
  disabled?: boolean;
  maxFiles: number;
  maxFileBytes: number;
  maxBatchBytes: number;
  onFiles: (files: File[]) => void;
}

/** Drag and drop area plus a regular file input. The input has no "accept"
 * filter on purpose: mobile browsers then offer camera roll, camera and files,
 * and the server decides what is supported from the content. */
export function Dropzone({ disabled, maxFiles, maxFileBytes, maxBatchBytes, onFiles }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState(false);
  const depth = useRef(0);

  const take = (list: FileList | null) => {
    if (!list || disabled) return;
    const files = Array.from(list).filter((f) => f.size >= 0);
    if (files.length) onFiles(files);
  };

  return (
    <div
      className="dropzone"
      data-active={active}
      onDragEnter={(e) => {
        e.preventDefault();
        depth.current += 1;
        setActive(true);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = disabled ? "none" : "copy";
      }}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setActive(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        setActive(false);
        take(e.dataTransfer.files);
      }}
    >
      <div className="dropzone-icon">
        <IconUpload size={26} />
      </div>
      <p className="dropzone-title">{active ? t("clean.dropActive") : t("clean.dropTitle")}</p>
      <p className="muted">{t("clean.dropOr")}</p>
      <button type="button" className="btn btn-primary" disabled={disabled} onClick={() => input.current?.click()}>
        {t("clean.dropButton")}
      </button>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          take(e.target.files);
          e.target.value = "";
        }}
      />
      <p className="muted small">
        {t("clean.dropHint", {
          count: maxFiles,
          size: formatBytes(maxFileBytes),
          batch: formatBytes(maxBatchBytes),
        })}
      </p>
    </div>
  );
}
