import type { CSSProperties } from "react";
import { cn } from "@/lib/utils";

type Size = "sm" | "md" | "lg" | "xl";

const SIZES: Record<Size, { digit: string; gap: string; hand: string; handGap: string }> = {
  sm: { digit: "text-base",                gap: "gap-1",   hand: "w-3 h-3",   handGap: "gap-[1px]" },
  md: { digit: "text-2xl",                 gap: "gap-1.5", hand: "w-4 h-4",   handGap: "gap-[2px]" },
  lg: { digit: "text-5xl",                 gap: "gap-2",   hand: "w-9 h-9",   handGap: "gap-[3px]" },
  xl: { digit: "text-[8rem] leading-none", gap: "gap-5",   hand: "w-24 h-24", handGap: "gap-[8px]" },
};

export function Wordmark({
  size = "md",
  className,
}: {
  size?: Size;
  className?: string;
}) {
  const s = SIZES[size];
  return (
    <span
      role="img"
      aria-label="67quant"
      className={cn(
        "inline-flex items-center font-mono font-bold tracking-tighter select-none",
        s.gap,
        className,
      )}
    >
      <span className={cn(s.digit, "leading-none")}>6</span>
      <span
        className={cn("inline-flex items-center", s.handGap)}
        aria-hidden="true"
      >
        <span className={cn("inline-block wobble-up", s.hand)}>
          <Hand />
        </span>
        <span className={cn("inline-block wobble-down", s.hand)}>
          <Hand />
        </span>
      </span>
      <span className={cn(s.digit, "leading-none")}>7</span>
    </span>
  );
}

/**
 * Open palm, fingers up. Stylized — 4 finger nubs over a flat-bottomed palm.
 * No thumb, no left/right asymmetry: two of these side by side read as
 * "two hands held out" without the iconography getting noisy at small sizes.
 */
function Hand(props: { style?: CSSProperties }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className="block w-full h-full"
      aria-hidden="true"
      {...props}
    >
      <rect x="1.8"  y="5.5"  width="3.4" height="10"  rx="1.7" />
      <rect x="6.6"  y="3.2"  width="3.4" height="12.3" rx="1.7" />
      <rect x="11.6" y="2.5"  width="3.4" height="13"  rx="1.7" />
      <rect x="16.6" y="4"    width="3.4" height="11.5" rx="1.7" />
      <rect x="0.7"  y="13.5" width="22.6" height="9"  rx="4" />
    </svg>
  );
}
