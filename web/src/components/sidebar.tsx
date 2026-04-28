"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  TrendingUp,
  ListOrdered,
  Bot,
  History,
  Activity,
  ShieldAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/bots", label: "Bots", icon: Bot },
  { href: "/positions", label: "Positions", icon: TrendingUp },
  { href: "/orders", label: "Orders", icon: ListOrdered },
  { href: "/trades", label: "Trades", icon: History },
  { href: "/signals", label: "Signals", icon: Activity },
  { href: "/audit", label: "Audit", icon: ShieldAlert },
] as const;

export function Sidebar() {
  const path = usePathname();
  return (
    <aside className="w-56 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-5 hidden md:block">
      <Link href="/" className="flex items-center gap-2 px-2 mb-6">
        <div className="w-7 h-7 rounded bg-[var(--color-accent)] flex items-center justify-center text-xs font-bold text-white">
          tb
        </div>
        <div>
          <div className="text-sm font-semibold tracking-tight">Trading Bot</div>
          <div className="text-[10px] text-[var(--color-text-subtle)] uppercase tracking-wider">
            multi-strategy
          </div>
        </div>
      </Link>
      <nav className="flex flex-col gap-0.5">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? path === "/" : path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 px-2.5 py-1.5 rounded text-sm transition-colors",
                active
                  ? "bg-[var(--color-surface-2)] text-[var(--color-text)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
              )}
            >
              <Icon size={15} />
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
