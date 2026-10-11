import type { ReactNode } from "react";
import { IconAlert, IconCheck, IconInfo } from "./Icons";

type Kind = "info" | "warn" | "error" | "ok";

export function Notice({ kind = "info", children, live }: { kind?: Kind; children: ReactNode; live?: boolean }) {
  const Icon = kind === "ok" ? IconCheck : kind === "info" ? IconInfo : IconAlert;
  const role = live ? (kind === "error" ? "alert" : "status") : undefined;
  return (
    <div className={`notice notice-${kind}`} role={role}>
      <Icon />
      <div>{children}</div>
    </div>
  );
}
