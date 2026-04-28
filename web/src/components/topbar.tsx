"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { LogOut, RefreshCcw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

export function Topbar() {
  const qc = useQueryClient();
  const router = useRouter();
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const { data: authStatus } = useQuery({ queryKey: ["authStatus"], queryFn: api.authStatus });
  const [refreshing, setRefreshing] = useState(false);

  const onRefresh = async () => {
    setRefreshing(true);
    await qc.invalidateQueries();
    setTimeout(() => setRefreshing(false), 400);
  };

  const onLogout = async () => {
    await api.logout();
    router.push("/login");
  };

  const isLive = health?.mode === "LIVE";
  return (
    <header className="border-b border-[var(--color-border)] bg-[var(--color-surface)] px-5 h-12 flex items-center gap-3">
      <div className="flex items-center gap-2">
        <span
          className="pill"
          style={{
            color: isLive ? "var(--color-negative)" : "var(--color-info)",
            background: isLive ? "rgba(248,81,73,0.12)" : "rgba(88,166,255,0.12)",
            borderColor: isLive ? "rgba(248,81,73,0.4)" : "rgba(88,166,255,0.4)",
          }}
        >
          {health?.mode ?? "…"}
        </span>
        <span
          className="pill"
          style={{
            color: "var(--color-text-muted)",
            background: "transparent",
            borderColor: "var(--color-border-strong)",
          }}
        >
          local
        </span>
      </div>
      <div className="flex-1" />
      <button
        onClick={onRefresh}
        className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] flex items-center gap-1.5 px-2 py-1 rounded hover:bg-[var(--color-surface-2)]"
      >
        <RefreshCcw size={14} className={refreshing ? "animate-spin" : ""} />
        Refresh
      </button>
      {authStatus?.required && (
        <button
          onClick={onLogout}
          className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] flex items-center gap-1.5 px-2 py-1 rounded hover:bg-[var(--color-surface-2)]"
        >
          <LogOut size={14} />
          Logout
        </button>
      )}
    </header>
  );
}
