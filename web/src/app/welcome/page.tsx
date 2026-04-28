import Link from "next/link";
import type { Metadata } from "next";
import { Wordmark } from "@/components/wordmark";

export const metadata: Metadata = {
  title: "67quant — multi-strategy quant trading",
  description:
    "Every alpha is 6-7. Multi-strategy paper-first algorithmic trading with an AI research layer.",
};

export default function WelcomePage() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="px-6 h-14 flex items-center justify-between border-b border-[var(--color-border)]">
        <Wordmark size="md" />
        <Link
          href="/login"
          className="text-sm px-3 py-1.5 rounded border border-[var(--color-border-strong)] hover:border-[var(--color-accent)] transition-colors"
        >
          Sign in
        </Link>
      </header>

      <main className="flex-1 flex flex-col items-center justify-center px-6 py-20 text-center">
        <Wordmark size="xl" className="text-[var(--color-positive)]" />
        <p className="mt-10 text-2xl md:text-3xl font-semibold max-w-2xl">
          Every alpha is 6-7.
        </p>
        <p className="mt-3 text-sm md:text-base text-[var(--color-text-muted)] max-w-xl">
          Multi-strategy paper-first algorithmic trading. Backtested,
          walk-forward validated, append-only audited. Probabilistic by
          construction.
        </p>
        <div className="mt-10 flex gap-3">
          <Link
            href="/login"
            className="px-5 py-2.5 text-sm font-medium rounded bg-[var(--color-accent)] text-white hover:opacity-90 transition-opacity"
          >
            Open dashboard
          </Link>
          <a
            href="#system"
            className="px-5 py-2.5 text-sm font-medium rounded border border-[var(--color-border-strong)] hover:border-[var(--color-accent)] transition-colors"
          >
            How it works
          </a>
        </div>
      </main>

      <section
        id="system"
        className="border-t border-[var(--color-border)] px-6 py-16 bg-[var(--color-surface)]"
      >
        <div className="max-w-5xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-10 text-sm">
          <div>
            <div className="text-xs uppercase tracking-wider text-[var(--color-text-subtle)] mb-2 num">
              01 / strategies
            </div>
            <h3 className="font-semibold text-base mb-2">
              Run them in parallel.
            </h3>
            <p className="text-[var(--color-text-muted)] leading-relaxed">
              Momentum, mean-reversion, congress trades, sentiment,
              cross-sectional — each with its own capital allocation, risk
              caps, schedule, and audit trail.
            </p>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wider text-[var(--color-text-subtle)] mb-2 num">
              02 / research
            </div>
            <h3 className="font-semibold text-base mb-2">
              An AI layer that proposes.
            </h3>
            <p className="text-[var(--color-text-muted)] leading-relaxed">
              A three-agent pipeline mines public AI-trading content and writes
              structured findings into the same database the trader runs on.
              LLMs propose. Python disposes.
            </p>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wider text-[var(--color-text-subtle)] mb-2 num">
              03 / safety
            </div>
            <h3 className="font-semibold text-base mb-2">
              Three gates before live.
            </h3>
            <p className="text-[var(--color-text-muted)] leading-relaxed">
              Paper-first by default. Two environment variables, ≥30-day Sharpe
              ≥ 1.0, and an orchestrator startup recheck stand between you and
              a real account.
            </p>
          </div>
        </div>
      </section>

      <section className="border-t border-[var(--color-border)] px-6 py-10">
        <div className="max-w-5xl mx-auto flex flex-wrap items-center gap-x-6 gap-y-2 text-xs font-mono text-[var(--color-text-subtle)] uppercase tracking-wider">
          <span className="text-[var(--color-text-muted)]">live bots</span>
          <span style={{ color: "var(--color-momentum)" }}>momentum</span>
          <span style={{ color: "var(--color-mean_reversion)" }}>mean_reversion</span>
          <span style={{ color: "var(--color-congress)" }}>congress</span>
          <span style={{ color: "var(--color-sentiment)" }}>sentiment</span>
          <span style={{ color: "var(--color-xs_momentum)" }}>xs_momentum</span>
        </div>
      </section>

      <footer className="border-t border-[var(--color-border)] px-6 py-6 flex items-center justify-between text-xs text-[var(--color-text-subtle)]">
        <Wordmark size="sm" />
        <span className="font-mono">markets are 6-7. so is everything else.</span>
      </footer>
    </div>
  );
}
