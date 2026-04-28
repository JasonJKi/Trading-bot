import { cn } from "@/lib/utils";

export function Table({ children, className }: React.HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto border border-[var(--color-border)] rounded-lg">
      <table className={cn("w-full text-sm", className)}>{children}</table>
    </div>
  );
}

export function THead({ children }: { children: React.ReactNode }) {
  return (
    <thead className="bg-[var(--color-surface-2)] text-[var(--color-text-muted)]">
      {children}
    </thead>
  );
}

export function TR({
  children,
  className,
}: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn(
        "border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-surface-2)]/50",
        className
      )}
    >
      {children}
    </tr>
  );
}

export function TH({
  children,
  className,
}: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "text-left text-[11px] uppercase tracking-wider font-semibold px-3 py-2 whitespace-nowrap",
        className
      )}
    >
      {children}
    </th>
  );
}

export function TD({
  children,
  className,
  align,
  num,
}: React.TdHTMLAttributes<HTMLTableCellElement> & { num?: boolean; align?: "left" | "right" }) {
  return (
    <td
      className={cn(
        "px-3 py-2 whitespace-nowrap",
        num && "num text-right",
        align === "right" && "text-right",
        className
      )}
    >
      {children}
    </td>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-center text-sm text-[var(--color-text-muted)] py-12 border border-dashed border-[var(--color-border)] rounded-lg">
      {children}
    </div>
  );
}
