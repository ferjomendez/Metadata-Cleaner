import esCL, { type Messages } from "./es-CL";

// To add a language: create i18n/<code>.ts exporting an object of type
// Messages and register it here.
const catalogs: Record<string, Messages> = { "es-CL": esCL };
export const locale = "es-CL";
export const messages: Messages = catalogs[locale];

type Params = Record<string, string | number>;

function lookup(path: string): unknown {
  return path.split(".").reduce<unknown>((node, key) => {
    if (node && typeof node === "object" && key in (node as Record<string, unknown>)) {
      return (node as Record<string, unknown>)[key];
    }
    return undefined;
  }, messages);
}

export function interpolate(template: string, params?: Params): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (k in params ? String(params[k]) : m));
}

/** Translate a dotted key, e.g. t("clean.dropHint", {count: 20}). */
export function t(path: string, params?: Params): string {
  const value = lookup(path);
  return typeof value === "string" ? interpolate(value, params) : path;
}

/** Message for an API/worker error code, with a generic fallback. */
export function errorMessage(code: string | null | undefined): string {
  if (!code) return messages.errors.generic;
  return messages.errors[code] ?? messages.errors.generic;
}

export function warningText(code: string): string {
  return messages.warnings[code] ?? code;
}

export function notVerifiedText(code: string): string {
  return messages.notVerified[code] ?? code;
}

export function checkText(code: string): string {
  return messages.checks[code] ?? code;
}

export function findingText(code: string): string {
  return messages.findings[code] ?? code;
}
