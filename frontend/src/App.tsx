import { useEffect, useRef, useState } from "react";
import { api, ApiError, setCsrf, setUnauthorizedHandler, type AppConfig, type Session } from "./api";
import { Notice } from "./components/Notice";
import { ThemeToggle } from "./components/ThemeToggle";
import { errorMessage, t } from "./i18n";
import { linkHandler, matchRoute, navigate, usePath } from "./lib/router";
import { Account } from "./pages/Account";
import { Admin } from "./pages/Admin";
import { Clean } from "./pages/Clean";
import { History } from "./pages/History";
import { JobView } from "./pages/JobView";
import { Login } from "./pages/Login";

export function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const path = usePath();
  const route = matchRoute(path);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setCsrf(null);
      setSession(null);
    });
    api
      .get<Session>("/api/auth/me")
      .then((s) => {
        setCsrf(s.csrf);
        setSession(s);
      })
      .catch(() => setSession(null));
  }, []);

  useEffect(() => {
    if (!session) return;
    api
      .get<AppConfig>("/api/config")
      .then(setConfig)
      .catch((err) => setError(errorMessage(err instanceof ApiError ? err.code : null)));
  }, [session]);

  // Move focus to the page title after client-side navigation (screen readers
  // otherwise do not notice the page changed) and keep the document title in sync.
  useEffect(() => {
    const titles: Record<string, string> = {
      clean: t("clean.title"),
      job: t("job.title"),
      history: t("history.title"),
      admin: t("admin.title"),
      account: t("account.title"),
      notFound: t("errors.not_found"),
    };
    document.title = session ? `${titles[route.name]} · ${t("app.name")}` : `${t("login.title")} · ${t("app.name")}`;
    const h = document.getElementById("main-title");
    if (h && document.activeElement !== h) h.focus({ preventScroll: false });
  }, [path, session, config]);

  async function logout() {
    try {
      await api.post("/api/auth/logout");
    } catch {
      /* session may already be gone */
    }
    setCsrf(null);
    setSession(null);
    setConfig(null);
    navigate("/", true);
  }

  let content: React.ReactNode;
  if (session === undefined) {
    content = (
      <div className="center">
        <span className="spinner" aria-hidden="true" />
        <span className="visually-hidden">{t("app.loading")}</span>
      </div>
    );
  } else if (session === null) {
    content = <Login onLogin={setSession} />;
  } else if (error) {
    content = <Notice kind="error">{error}</Notice>;
  } else if (!config) {
    content = <p className="muted">{t("app.loading")}</p>;
  } else {
    switch (route.name) {
      case "clean":
        content = <Clean config={config} />;
        break;
      case "job":
        content = <JobView key={route.id} id={route.id} config={config} />;
        break;
      case "history":
        content = <History config={config} />;
        break;
      case "account":
        content = <Account user={session.user} />;
        break;
      case "admin":
        content = session.user.is_admin ? <Admin me={session.user} /> : <NotFound />;
        break;
      default:
        content = <NotFound />;
    }
  }

  const navItems: { href: string; label: string; active: boolean }[] = session
    ? [
        { href: "/", label: t("app.nav.clean"), active: route.name === "clean" || route.name === "job" },
        { href: "/historial", label: t("app.nav.history"), active: route.name === "history" },
        ...(session.user.is_admin ? [{ href: "/admin", label: t("app.nav.admin"), active: route.name === "admin" }] : []),
        { href: "/cuenta", label: t("app.nav.account"), active: route.name === "account" },
      ]
    : [];

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        {t("app.skip")}
      </a>
      <header className="site-header">
        <div className="container">
          <a className="wordmark" href="/" onClick={linkHandler("/")} aria-label={t("app.name")}>
            <span className="logomark" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span className="bar" aria-hidden="true">
              Metadata
            </span>
            <span className="clean" aria-hidden="true">
              Cleaner
            </span>
          </a>
          {session && (
            <nav className="nav" aria-label={t("app.nav.label")}>
              {navItems.map((n) => (
                <a key={n.href} href={n.href} onClick={linkHandler(n.href)} aria-current={n.active ? "page" : undefined}>
                  {n.label}
                </a>
              ))}
              <button type="button" onClick={logout}>
                {t("app.nav.logout")}
              </button>
            </nav>
          )}
          <ThemeToggle />
        </div>
      </header>
      <main id="main" ref={mainRef} tabIndex={-1}>
        <div className="container">{content}</div>
      </main>
      <footer className="site-footer">
        <div className="container">{t("app.footer")}</div>
      </footer>
    </div>
  );
}

function NotFound() {
  return (
    <header className="page-head">
      <h1 id="main-title" tabIndex={-1}>
        {t("errors.not_found")}
      </h1>
      <p>
        <a href="/" onClick={linkHandler("/")}>
          {t("job.back")}
        </a>
      </p>
    </header>
  );
}
