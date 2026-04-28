# System architecture

The north-star design. `ARCHITECTURE.md` is the operating manual ("how the
running system works today"); this is "what we are building toward and why."

## Five principles

These are load-bearing. Everything else is a consequence.

1. **Trust boundary: decisions vs. execution.** LLMs *propose*; deterministic
   Python *disposes*. No LLM output ever directly causes a broker order. Every
   agent emits a typed schema → goes through validated Python → only then can
   touch the broker layer. This is the single most important architectural
   choice in the system.

2. **The DB is the spine.** No agent has private state. Every coordinated
   action lives in one SQLite (eventually Postgres) database. The system is
   replayable, debuggable, and rebuildable from the DB alone. This already
   holds for trading; the agent layer must follow.

3. **One-way data flow per cycle.** Research → Strategy → Backtest → Paper →
   Live → Analysis → (back to Research). Cycles are fine, but each arrow is
   an explicit pipeline step that writes to the DB. No hidden dependencies.

4. **Two schedulers, never one.** Trading scheduler is real-time and must
   never block on an LLM call. Analytical scheduler is slow, async, can call
   LLMs freely. They communicate *only* through the DB.

5. **Versioning + audit on everything.** Strategies, agents, templates,
   findings, allocator decisions — all stamped with a version. Audit events
   are append-only. "Why did this bot do X on Tuesday at 3 PM?" should be
   answerable from the DB alone.

## The system in one picture

```
                     ┌─────────────────────────────────────┐
                     │       ANALYTICAL SCHEDULER          │
                     │  (slow, can block on LLMs)          │
                     └─────────────────────────────────────┘
                                     │
   DISCOVERY ──────► IDEATION ──────► VALIDATION ──────► PROMOTION
   ─────────         ────────         ──────────         ──────────
   CIO Agent         Synthesis        Backtest +         Graduation
   Research Agent  ┌►Agent            Walk-forward       Gate
                   │                     │                    │
                   │ writes              │ writes             │ writes
                   ▼                     ▼                    ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                       DATA PLANE (DB)                            │
   │                                                                   │
   │  Findings → StrategySpec → BacktestReport → Strategy(version)    │
   │     ↑          │                                       │          │
   │     │          ▼                                       ▼          │
   │  ResearchDocument         ┌──────────────────────────────────┐   │
   │                           │ Signal → Order → Trade → Position │   │
   │                           │            EquitySnapshot         │   │
   │  HealthReport ←───────────┤            AuditEvent             │   │
   │  AllocationDecision ←─────┤                                   │   │
   │                           └──────────────────────────────────┘   │
   └─────────────────────────────────────────────────────────────────┘
                   ▲                     │                    ▲
                   │ reads               │ writes             │ reads
   ANALYSIS ◄──── PAPER/LIVE TRADING ◄────────────── EXECUTION
   ─────────       ────────────────                  ──────────
   Per-bot Analyst Orchestrator                      Risk Officer (det.)
   Portfolio Agent Bots → Broker                     Reconciler

                     ┌─────────────────────────────────────┐
                     │        TRADING SCHEDULER             │
                     │  (real-time, never blocks on LLM)   │
                     └─────────────────────────────────────┘
```

## The six domains

| Domain | What it owns | Status today |
|---|---|---|
| **Discovery** | Find things worth doing | L1 Research agent (done); CIO + news monitor (TODO) |
| **Strategy** | Encode ideas as testable logic | Strategy ABC, walk-forward (done); template library + synthesis (TODO) |
| **Execution** | Run bots safely | Orchestrator, broker, reconciler, ledger (done); crypto track (TODO) |
| **Risk** | Don't blow up | Caps, drawdown gates, regime (done); cross-bot correlation (TODO) |
| **Analysis** | Make the system self-aware | Metrics, audit log, dashboard (done); per-bot analyst, portfolio agent (TODO) |
| **Governance** | Meta layer | Config, paper→live gate (done); CIO, agent eval registry, cost accounting (TODO) |

## The six modularity contracts

Every component implements one of these. New features = new implementations of
existing interfaces. If a feature doesn't fit one of these six, you've either
modeled it wrong or genuinely found a new dimension (rare).

| Contract | Method | Examples |
|---|---|---|
| `Strategy` | `target_positions(ctx) → list[TargetPosition]` | momentum, mean_reversion, sentiment, congress |
| `BrokerAdapter` | `submit / cancel / positions` | Alpaca; future: IBKR, Binance, Coinbase |
| `SourceAdapter` | `search(query) → list[DocumentRow]` | reddit, web, arxiv, github, hackernews, youtube |
| `StrategyTemplate` | `instantiate(spec) → Strategy` | MovingAverageCross, MeanReversionRSI, MomentumZScore, VolBreakout |
| `Analyst` | `analyze(window) → Report` | per-bot analyst, portfolio analyst, CIO |
| `Allocator` | `allocate(equity, bots) → {bot_id: cap}` | equal-weight (today), correlation-aware (future) |

## Lifecycle of a strategy

The connecting thread that ties every domain together. Each transition is a
DB write; each gate is an explicit policy.

```
1. CIO topic        → ResearchQuery row
2. Research run     → ResearchDocument + ResearchFinding rows
3. Finding selected → StrategySpec row (template_id + parameter dict)
                      ↑ synthesis agent writes here
4. Backtest         → BacktestReport row (Sharpe, DD, overfit gap)
                      ↑ deterministic Python; gate refuses if metrics fail
5. Walk-forward opt → StrategySpec.params updated with robust params
6. Promotion        → Strategy registered; BotStatus row = enabled, paper
7. Paper trading    → Signal/Order/Trade/EquitySnapshot rows accumulate
8. Per-bot analyst  → HealthReport row nightly
9. Graduation gate  → BotStatus.paper_validated_at = now (or refuse)
10. Live trading    → same trading scheduler, real account
11. Alpha decay     → analyst flags; portfolio agent recommends pause
12. Retirement      → BotStatus = disabled; data preserved for replay
```

## Concurrency + scheduling

Two schedulers, never crossed:

| Scheduler | Lives where | Cadence | What it does |
|---|---|---|---|
| **Trading** | `src/core/orchestrator.py` (existing) | seconds–minutes | Cycle each bot's `target_positions(ctx)`, reconcile orders. Pure Python. **Never blocks on LLM.** |
| **Analytical** | `src/research/scheduler.py` (TODO) | hours–days | Run research agents, synthesis, per-bot analyst, portfolio analyst, CIO. Uses LLMs freely. |

For solo-fund scale: same machine, separate processes, both reading/writing
the same SQLite. When SQLite contention shows up, swap to Postgres without
changing application code. Beyond that: queue worker (Celery / Arq).

## Trust boundary in detail

Where can an LLM's output cross into doing things?

```
LLM proposes  ─────►  Pydantic validates  ─────►  Deterministic Python decides
─────────────         ─────────────────          ────────────────────────────
ResearchPlan          schema validation           store in ResearchQuery.plan
StrategySpec          template + param ranges     store in StrategySpec, run
                                                  backtest gate; gate decides
HealthReport          structured output           store; *recommend* to user
AllocationDecision    structured output           *recommend* — Risk Officer
                                                  enforces hard caps on top
```

**Critical rule:** an LLM proposal that violates a hard cap (per-position %,
drawdown, etc.) is rejected at the validation step. The validation code is
the same code the orchestrator uses. There is no path where an LLM's output
bypasses the deterministic risk checks.

## Versioning discipline

Three things have explicit versions:

- `Strategy.version` — bumped when signal logic changes. Stamped on every
  Signal/Order/Trade so historical replay stays meaningful.
- `StrategyTemplate.version` — bumped when the template's mechanism changes.
- `Agent.version` — bumped when the prompt or output schema changes.

When you upgrade a versioned component, you can run shadow A/B (new version
in parallel, no orders). The eval registry (TODO) tracks which versions
produced which outputs.

## DB schema, layered view

```
Idea layer        ResearchQuery ─┬─► ResearchDocument
                                 └─► ResearchFinding

Strategy layer    StrategySpec ──► BacktestReport
                       │
                       ▼
                  registered Strategy (in src/bots/* or instantiated from a
                  template) → has BotStatus row

Execution layer   Signal → Order → Trade → BotPosition
                  EquitySnapshot (per cycle)

State layer       AuditEvent (append-only)

Analysis layer    HealthReport (per bot, per day)
                  AllocationDecision (per portfolio, per day)
                  CIODirective (weekly meta-actions)
```

Foreign keys are intentionally light (SQLite + replay-friendly). Joins go
through stable string ids: `strategy_id`, `query_id`, `template_id`.

## What gets built next, in priority order

1. **`StrategyTemplate` library** — close the research → deploy loop.
2. **L2 Synthesis agent** — Finding → StrategySpec.
3. **L3 Backtest upgrades** — slippage model, deflated Sharpe, regime-stratified.
4. **L5 Per-bot Analyst** — nightly HealthReport.
5. **L6 Portfolio Agent** — daily AllocationDecision.
6. **Crypto track** — Freqtrade behind a `BrokerAdapter`.
7. **L0 CIO Agent** — last; needs everything below to work.

## Scaling story

Each transition should require zero rewrites of the trusted core:

| Stage | What changes | What stays |
|---|---|---|
| Solo paper | nothing | the whole pipeline |
| Solo live, modest capital | live confirm token; Alpaca real | rest |
| Friends & family pool | per-LP equity tracking; reporting | strategy/execution untouched |
| Registered fund | compliance reporting, audit retention | core pipeline; just operational rigor |

The agents are leverage. The deterministic core is what you bet on.
