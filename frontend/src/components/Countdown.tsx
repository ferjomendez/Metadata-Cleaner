import { useEffect, useState } from "react";
import { t } from "../i18n";
import { formatCountdown } from "../lib/format";
import { IconClock } from "./Icons";

/** Retention countdown. The ticking digits are hidden from screen readers;
 * an accessible label with minute precision is exposed instead, so assistive
 * technology is not flooded with updates every second. */
export function Countdown({ expiresAt, skewMs, onExpire }: { expiresAt: string; skewMs: number; onExpire?: () => void }) {
  const target = new Date(expiresAt).getTime();
  const [now, setNow] = useState(() => Date.now() + skewMs);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() + skewMs), 1000);
    return () => window.clearInterval(id);
  }, [skewMs]);
  const remaining = Math.max(0, (target - now) / 1000);
  useEffect(() => {
    if (remaining <= 0 && onExpire) onExpire();
  }, [remaining <= 0]);
  if (remaining <= 0) return <span className="muted">{t("job.countdownExpired")}</span>;
  const minutes = Math.ceil(remaining / 60);
  const text = formatCountdown(remaining);
  return (
    <span className="countdown">
      <IconClock />
      <span aria-hidden="true">
        {t("job.countdown", { time: "" }).trim()} <span className="digits">{text}</span>
      </span>
      <span className="visually-hidden">{t("job.countdown", { time: `${minutes} min` })}</span>
    </span>
  );
}
