import { cn } from "@/lib/utils";

export function Card({
  className,
  accent,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { accent?: string }) {
  return (
    <div
      className={cn(
        "bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg",
        className
      )}
      style={accent ? { borderLeft: `3px solid ${accent}` } : undefined}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  right,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between px-4 pt-3 pb-2 border-b border-[var(--color-border)]">
      <div>
        <div className="text-sm font-semibold tracking-tight">{title}</div>
        {subtitle && (
          <div className="text-xs text-[var(--color-text-muted)] mt-0.5">{subtitle}</div>
        )}
      </div>
      {right}
    </div>
  );
}

export function CardBody({ className, children }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)}>{children}</div>;
}

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] font-semibold mt-6 mb-2">
      {children}
    </div>
  );
}
