import { useEffect, useState } from "react";

// Minimal client-side routing on the History API (no extra dependency).

export function navigate(path: string, replace = false) {
  if (path === window.location.pathname) return;
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function usePath(): string {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

export type Route =
  | { name: "clean" }
  | { name: "job"; id: string }
  | { name: "history" }
  | { name: "admin" }
  | { name: "account" }
  | { name: "notFound" };

export function matchRoute(path: string): Route {
  if (path === "/" || path === "") return { name: "clean" };
  const job = /^\/trabajos\/([0-9a-f]{32})\/?$/.exec(path);
  if (job) return { name: "job", id: job[1] };
  if (path === "/historial") return { name: "history" };
  if (path === "/admin") return { name: "admin" };
  if (path === "/cuenta") return { name: "account" };
  return { name: "notFound" };
}

/** Anchor click handler that keeps navigation client-side. */
export function linkHandler(path: string) {
  return (e: React.MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(path);
  };
}
