import { cn } from "@/lib/utils";

export function Metric({
  label,
  value,
  delta,
  deltaTone,
  loading,
  accent,
  className,
}: {
  label: string;
  value: React.ReactNode;
  delta?: React.ReactNode;
  deltaTone?: "pos" | "neg" | "neutral";
  loading?: boolean;
  accent?: string;
  className?: string;
}) {
  const deltaColor =
    deltaTone === "pos"
      ? "text-[var(--color-positive)]"
      : deltaTone === "neg"
      ? "text-[var(--color-negative)]"
      : "text-[var(--color-text-muted)]";
  return (
    <div
      className={cn(
        "bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg px-4 py-3",
        className
      )}
      style={accent ? { borderLeft: `3px solid ${accent}` } : undefined}
    >
      <div className="text-[11px] uppercase tracking-wider text-[var(--color-text-muted)] font-semibold">
        {label}
      </div>
      <div className="num text-2xl font-semibold mt-1.5 leading-none">
        {loading ? <span className="text-[var(--color-text-subtle)]">…</span> : value}
      </div>
      {delta !== undefined && (
        <div className={cn("num text-xs mt-1.5", deltaColor)}>{delta}</div>
      )}
    </div>
  );
}
