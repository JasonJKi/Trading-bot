"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  // First call to a protected endpoint will tell us if we're authenticated.
  const probe = useQuery({
    queryKey: ["authProbe"],
    queryFn: api.account,
    retry: false,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (probe.error && (probe.error as { code?: number }).code === 401) {
      router.replace("/welcome");
    }
  }, [probe.error, router]);

  if (probe.isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-[var(--color-text-muted)] text-sm">
        Loading…
      </div>
    );
  }
  if (probe.error && (probe.error as { code?: number }).code === 401) return null;
  return <>{children}</>;
}
