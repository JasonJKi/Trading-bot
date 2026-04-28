# Overview

A multi-strategy paper-first algorithmic trading platform with an AI
agent layer for research, designed with the operational rigor of a system
that will eventually trade real money.

> The 5-minute version. Send this to someone who has never seen the repo.
> For anything deeper, follow the links at the end.

## What this is

A self-contained system that:

1. **Runs multiple trading strategies in parallel** on an Alpaca paper
   account — each with its own capital allocation, risk caps, schedule,
   and audit trail.
2. **Mines public AI-trading content** (Reddit, arXiv, GitHub,
   HackerNews, YouTube, the web) using a three-agent PydanticAI pipeline
   (planner → researcher → synthesizer) backed by Gemini, and writes
   structured findings to the same database the trading system uses.
3. **Surfaces everything in a Next.js + FastAPI dashboard** — live
   equity, per-bot performance, trades, the append-only audit log, and
   research findings with citations.
4. **Refuses to do anything dangerous by default.** Three independent
   gates separate paper from live trading. LLM outputs never reach the
   broker without flowing through deterministic Python that shares the
   same risk code as the orchestrator.

## What it does NOT do

- It does **not** auto-deploy strategies that the research agent
  surfaces. Findings are an idea backlog; graduation to a live strategy
  is manual, backtested, and gate-checked.
- It does **not** move real money unless two environment variables are
  set, every bot has been paper-validated for ≥30 days at Sharpe ≥ 1.0,
  and the orchestrator's startup recheck passes.
- It does **not** keep private state inside any agent or scheduler. The
  DB is the spine; the system is replayable and debuggable from the DB
  alone.

## Who this is for

- **The operator (you):** one person running multiple strategies side by
  side, watching which survive volatile periods before risking real
  capital.
- **As a reference design:** the architectural patterns here — the trust
  boundary between LLMs and execution, the two-scheduler split, the
  append-only audit, Pydantic-validated agent outputs, version-stamped
  artifacts — are the same patterns any production-grade AI agent
  deployment needs. The codebase is intentionally a *demo of how to do
  this well*, not just a trading bot.

## What's in the box

```
src/
  config.py              env-driven settings + live-trading guard
  core/                  orchestrator, strategy ABC, broker, store,
                         metrics, risk, regime, alerter, allocator,
                         reconciler, healthz, logging
  bots/                  momentum, cross_momentum, mean_reversion,
                         congress, sentiment
  templates/             StrategyTemplate catalog (synthesis target)
  data/                  yfinance bars, news, congress, feature adapters
  api/                   FastAPI dashboard backend (read-only over the DB)
  research/              opt-in 3-agent research pipeline + source adapters
  backtest/              walk-forward runner + Optuna optimizer
  cli.py                 run | backtest | optimize | graduate | pause |
                         enable | status | research | templates
web/                     Next.js 15 + Tailwind dashboard
tests/                   pytest suite (no network required)
deploy/                  laptop → Mac-mini deploy automation
```

## The high-level shape

```
┌──────────────────────────┐         ┌──────────────────────────┐
│   TRADING SCHEDULER      │         │  ANALYTICAL SCHEDULER    │
│   (real-time, never      │         │  (slow, async, can call  │
│    blocks on an LLM)     │         │    LLMs freely)          │
└────────────┬─────────────┘         └────────────┬─────────────┘
             │                                    │
             ▼                                    ▼
       Strategy.target_positions(ctx)    research planner →
             │                            researcher → synthesizer
             ▼                                    │
       BrokerAdapter ── risk caps ──► Alpaca      ▼
             │                            ResearchDocument /
             ▼                                ResearchFinding
        Order ─────► Reconciler ─────► Trade
                                │
                                ▼
                  ┌─────────────────────────────┐
                  │      DATA PLANE (DB)        │
                  │   the spine of the system    │
                  └─────────────────────────────┘
                           ▲
                           │
                  Next.js + FastAPI
                  read-only dashboard
```

The two schedulers never share a process and never call each other
directly. They communicate exclusively by writing rows the other side can
read — which makes the system replayable, restart-safe, and debuggable
from the DB alone.

## How a strategy moves through the system

```
1. Topic queued                  → ResearchQuery row
2. Research agent runs           → ResearchDocument + ResearchFinding rows
3. Finding selected              → StrategySpec (template + params)
4. Backtest                      → BacktestReport; gate refuses if metrics fail
5. Walk-forward                  → robust params confirmed
6. Promotion                     → Strategy registered, paper-trading
7. Per-bot analyst               → nightly HealthReport
8. Graduation gate               → 30d + Sharpe ≥ 1.0 → paper_validated_at
9. Live trading                  → same scheduler, real account
10. Alpha decay                  → analyst flags; portfolio agent recommends pause
11. Retirement                   → BotStatus = disabled; data preserved for replay
```

Stages 1–4 are partly automated; the rest is human-gated. Every transition
writes a row; every meaningful decision writes an `AuditEvent`.

## Why this is not just a notebook

| A Jupyter notebook | This |
|---|---|
| State lives in memory | State lives in the DB |
| Crashes lose work | Restart resumes |
| LLMs decide directly | LLMs propose, Python disposes |
| One-off | Versioned, replayable |
| Backtest ≠ deploy | Same `Strategy` class for backtest + live |
| "What happened on Tuesday at 3 PM?" — go re-run it | `audit_events` answers it |

## Where to go next

- **The engineering bar and current audit** → [`production-baseline.md`](./production-baseline.md)
- **The north-star architecture and five principles** → [`system-architecture.md`](./system-architecture.md)
- **How the running system works today** → [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
- **What we're building next** → [`roadmap.md`](./roadmap.md)
- **Run it locally** → [`../README.md`](../README.md)
- **Ops runbook (backups, secrets, DR)** → [`../OPS.md`](../OPS.md)
