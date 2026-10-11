import { useEffect, useState } from "react";
import { t } from "../i18n";
import { IconAuto, IconMoon, IconSun } from "./Icons";

type Theme = "system" | "light" | "dark";
const KEY = "mc-theme";

export function readTheme(): Theme {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    applyTheme(theme);
    try {
      if (theme === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, theme);
    } catch {
      /* storage unavailable: theme still applies for this page view */
    }
  }, [theme]);
  const next: Theme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Icon = theme === "light" ? IconSun : theme === "dark" ? IconMoon : IconAuto;
  const label = t(`app.theme.${theme}`);
  return (
    <button type="button" className="theme-toggle" onClick={() => setTheme(next)} title={label}>
      <Icon />
      <span className="visually-hidden">
        {t("app.theme.label")}: {label}
      </span>
    </button>
  );
}
