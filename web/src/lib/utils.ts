import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtUSD(n: number, opts: { compact?: boolean; sign?: boolean } = {}) {
  const { compact = false, sign = false } = opts;
  const s = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : 2,
  }).format(n);
  return sign && n > 0 ? `+${s}` : s;
}

export function fmtPct(n: number, digits = 2, sign = false) {
  const s = `${n.toFixed(digits)}%`;
  return sign && n > 0 ? `+${s}` : s;
}

export function fmtNum(n: number, digits = 2) {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function deltaTone(n: number): "pos" | "neg" | "neutral" {
  if (n > 0) return "pos";
  if (n < 0) return "neg";
  return "neutral";
}

export function botColorVar(strategyId: string): string {
  return `var(--color-${strategyId}, var(--color-accent))`;
}

export function relativeTime(target: Date | string | null): string {
  if (!target) return "—";
  const t = typeof target === "string" ? new Date(target) : target;
  const diff = Math.floor((t.getTime() - Date.now()) / 1000);
  const abs = Math.abs(diff);
  const sign = diff < 0 ? "ago" : "in";
  if (abs < 60) return `${sign === "ago" ? "" : "in "}${abs}s${sign === "ago" ? " ago" : ""}`;
  if (abs < 3600) return `${sign === "ago" ? "" : "in "}${Math.floor(abs / 60)}m${sign === "ago" ? " ago" : ""}`;
  if (abs < 86400)
    return `${sign === "ago" ? "" : "in "}${Math.floor(abs / 3600)}h ${Math.floor((abs % 3600) / 60)}m${sign === "ago" ? " ago" : ""}`;
  return `${sign === "ago" ? "" : "in "}${Math.floor(abs / 86400)}d${sign === "ago" ? " ago" : ""}`;
}
