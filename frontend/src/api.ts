// Thin client for the JSON API. Every state-changing request carries the
// session's CSRF token; cookies are HttpOnly and never touched from JS.

export class ApiError extends Error {
  constructor(public code: string, public status: number) {
    super(code);
  }
}

let csrfToken: string | null = null;
export function setCsrf(token: string | null) {
  csrfToken = token;
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError("network", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const code = typeof data?.error === "string" ? data.error : "generic";
    if (res.status === 401 && onUnauthorized && !url.endsWith("/auth/login")) onUnauthorized();
    throw new ApiError(code, res.status);
  }
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  patch: <T>(url: string, body: unknown) => request<T>("PATCH", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
};

/** Upload one file as a raw body, reporting progress. Uses XHR because fetch
 * has no upload progress events. */
export function uploadFile(
  jobId: string,
  file: File,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<JobFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/jobs/${jobId}/files`);
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let data: { error?: string } & Partial<JobFile> = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* ignore */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as JobFile);
      else reject(new ApiError(data.error ?? "generic", xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("network", 0));
    xhr.onabort = () => reject(new ApiError("incomplete_upload", 0));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(file);
  });
}

// ---- types mirrored from the backend -------------------------------------------

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
}

export interface Session {
  user: User;
  csrf: string;
}

export type Mode = "conservative" | "deep";

export interface ModePolicy {
  procedure: string;
  warnings: string[];
  not_verified: string[];
}

export interface FormatPolicy {
  family: string;
  label: string;
  extensions: string[];
  conservative: ModePolicy;
  deep: ModePolicy;
}

export interface AppConfig {
  limits: { max_file_bytes: number; max_files_per_batch: number; max_batch_bytes: number };
  retention_minutes: number;
  history_retention_days: number;
  modes: Mode[];
  formats: Record<string, FormatPolicy>;
}

export interface Summary {
  removed: string[];
  retained: string[];
  not_verified: string[];
  warnings: string[];
  checks: { code: string; ok: boolean }[];
  findings: string[];
  size_before: number;
  size_after: number;
}

export interface JobFile {
  id: string;
  name: string;
  format: string | null;
  size: number;
  status: string;
  reject_code: string | null;
  error_code: string | null;
  output_name: string | null;
  output_size: number | null;
  downloadable: boolean;
  summary: Summary | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Job {
  id: string;
  mode: Mode | null;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  expires_at: string;
  deletion_pending: boolean;
  server_time: string;
  files: JobFile[];
}

export interface ReportItem {
  key: string;
  value: string;
}

export interface Report {
  format: string;
  mode: Mode;
  procedure: string;
  removed: ReportItem[];
  retained: ReportItem[];
  not_verified: string[];
  warnings: string[];
  checks: { code: string; ok: boolean; detail: string }[];
  findings: { code: string; value: string }[];
  size_before: number;
  size_after: number;
}
