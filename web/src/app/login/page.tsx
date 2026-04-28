"use client";

import { api } from "@/lib/api";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Wordmark } from "@/components/wordmark";

export default function LoginPage() {
  const router = useRouter();
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // If auth is disabled, bounce straight in.
  useEffect(() => {
    api.authStatus().then((s) => {
      if (!s.required) router.replace("/");
    });
  }, [router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setLoading(true);
    try {
      await api.login(pw);
      router.replace("/");
    } catch {
      setErr("Incorrect password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-6"
      >
        <div className="flex items-end justify-between gap-3 mb-5">
          <Wordmark size="md" />
          <div className="text-xs text-[var(--color-text-muted)] pb-1">
            Sign in to continue
          </div>
        </div>
        <label className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] font-semibold">
          Password
        </label>
        <input
          autoFocus
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          className="mt-1.5 w-full bg-[var(--color-bg)] border border-[var(--color-border-strong)] rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)]"
        />
        {err && <div className="text-xs text-[var(--color-negative)] mt-2">{err}</div>}
        <button
          type="submit"
          disabled={loading}
          className="mt-4 w-full bg-[var(--color-accent)] text-white text-sm font-medium py-2 rounded hover:opacity-90 disabled:opacity-50"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
