import { locale, messages } from "../i18n";

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "–";
  const u = messages.units;
  const nf = (v: number, d: number) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(v);
  if (bytes < 1024) return `${nf(bytes, 0)} ${u.b}`;
  if (bytes < 1024 ** 2) return `${nf(bytes / 1024, 1)} ${u.kb}`;
  if (bytes < 1024 ** 3) return `${nf(bytes / 1024 ** 2, 1)} ${u.mb}`;
  return `${nf(bytes / 1024 ** 3, 2)} ${u.gb}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(d);
}

/** mm:ss or h:mm:ss for a non-negative number of seconds. */
export function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

/** Offset between the server clock and the local clock, in ms. */
export function clockSkew(serverIso: string): number {
  const server = new Date(serverIso).getTime();
  return Number.isNaN(server) ? 0 : server - Date.now();
}

/** Split "Group:Tag" into its parts for display. */
export function splitKey(key: string): { group: string; tag: string } {
  const i = key.indexOf(":");
  return i < 0 ? { group: "", tag: key } : { group: key.slice(0, i), tag: key.slice(i + 1) };
}
