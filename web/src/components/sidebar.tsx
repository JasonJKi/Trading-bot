"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  TrendingUp,
  ListOrdered,
  Bot,
  History,
  Activity,
  ShieldAlert,
  Lock,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Wordmark } from "@/components/wordmark";

// `protected: true` → only rendered when the user is signed in. The data
// source for these pages is auth-gated; showing them to a signed-out
// visitor would just produce broken empty tables.
const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard, protected: false },
  { href: "/bots", label: "Bots", icon: Bot, protected: false },
  { href: "/positions", label: "Positions", icon: TrendingUp, protected: true },
  { href: "/orders", label: "Orders", icon: ListOrdered, protected: true },
  { href: "/trades", label: "Trades", icon: History, protected: true },
  { href: "/signals", label: "Signals", icon: Activity, protected: true },
  { href: "/audit", label: "Audit", icon: ShieldAlert, protected: true },
] as const;

export function Sidebar() {
  const path = usePathname();
  const { data: authStatus } = useQuery({
    queryKey: ["authStatus"],
    queryFn: api.authStatus,
  });
  const inPublicView = authStatus?.required === true && authStatus.authenticated === false;
  const visibleNav = inPublicView ? NAV.filter((n) => !n.protected) : NAV;

  return (
    <aside className="w-56 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-5 hidden md:block">
      <Link
        href="/"
        className="flex items-center gap-2 px-2 mb-6 text-[var(--color-text)]"
      >
        <Wordmark size="sm" />
        <div className="text-[10px] text-[var(--color-text-subtle)] uppercase tracking-wider leading-tight">
          quant
          <br />
          multi-strategy
        </div>
      </Link>
      <nav className="flex flex-col gap-0.5">
        {visibleNav.map(({ href, label, icon: Icon }) => {
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
      {inPublicView && (
        <div className="mt-6 px-2.5 py-2 text-[11px] leading-snug text-[var(--color-text-subtle)] border-t border-[var(--color-border)] pt-4 flex gap-1.5">
          <Lock size={12} className="mt-0.5 shrink-0" />
          <span>
            Public view. Sign in to see live positions, orders, trades, signals,
            and the audit log.
          </span>
        </div>
      )}
    </aside>
  );
}
