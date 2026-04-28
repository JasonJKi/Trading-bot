import { cn } from "@/lib/utils";

const TONES: Record<string, { fg: string; bg: string; bd: string }> = {
  ok: { fg: "var(--color-positive)", bg: "rgba(63,185,80,0.12)", bd: "rgba(63,185,80,0.4)" },
  warn: { fg: "var(--color-warn)", bg: "rgba(210,153,34,0.12)", bd: "rgba(210,153,34,0.4)" },
  bad: { fg: "var(--color-negative)", bg: "rgba(248,81,73,0.12)", bd: "rgba(248,81,73,0.4)" },
  info: { fg: "var(--color-info)", bg: "rgba(88,166,255,0.12)", bd: "rgba(88,166,255,0.4)" },
  neutral: { fg: "var(--color-text-muted)", bg: "transparent", bd: "var(--color-border-strong)" },
};

export function Pill({
  tone = "neutral",
  children,
  className,
}: {
  tone?: keyof typeof TONES;
  children: React.ReactNode;
  className?: string;
}) {
  const t = TONES[tone];
  return (
    <span
      className={cn("pill", className)}
      style={{ color: t.fg, background: t.bg, borderColor: t.bd }}
    >
      {children}
    </span>
  );
}

export function regimeTone(regime: string) {
  if (regime === "bull") return "ok" as const;
  if (regime === "bear") return "warn" as const;
  if (regime === "crisis") return "bad" as const;
  return "neutral" as const;
}

export function statusTone(status: string) {
  if (status === "filled") return "ok" as const;
  if (status === "rejected" || status === "expired") return "bad" as const;
  if (status === "partially_filled") return "warn" as const;
  if (status === "accepted" || status === "new") return "info" as const;
  return "neutral" as const;
}

export function botStateTone(state: string) {
  if (state === "enabled") return "ok" as const;
  if (state === "paused") return "warn" as const;
  if (state === "disabled") return "bad" as const;
  return "neutral" as const;
}

export function severityTone(s: string) {
  if (s === "info") return "info" as const;
  if (s === "warning") return "warn" as const;
  if (s === "error" || s === "critical") return "bad" as const;
  return "neutral" as const;
}
