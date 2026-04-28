# Documentation

The docs are layered by *audience* and *update cadence*. Read them in
roughly this order when onboarding; otherwise jump to whichever one
answers your current question.

## The doc set

| Doc | Question it answers | Update cadence |
|---|---|---|
| [overview.md](./overview.md) | What is this thing, what does it do, who is it for? | Rarely (vision shifts) |
| [system-architecture.md](./system-architecture.md) | What are we building toward and why? (north star) | Rarely (principle changes) |
| [production-baseline.md](./production-baseline.md) | What's the engineering bar and how close are we? | Per audit (~quarterly) |
| [roadmap.md](./roadmap.md) | What are we building next, in what order? | Every phase |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | How does the running system work *today*? (operating manual) | Every change to the running system |
| [../OPS.md](../OPS.md) | What do I do when something breaks? (runbook) | When ops procedures change |
| [data-sources.md](./data-sources.md) | How do I add a new data source? | When the source contract changes |
| [research-agent.md](./research-agent.md) | How does the research agent work internally? | When the research subsystem changes |

## How they relate

Vision → reality, in three layers:

1. **`overview.md`** — outside-in. The 5-minute "what is this and why."
   Send this to someone who has never seen the repo. No code paths.
2. **`system-architecture.md`** — north star. Five principles, the system
   in one picture, the strategy lifecycle. The *aspiration*.
3. **`ARCHITECTURE.md`** — current operating manual. The *reality* you
   can grep for today.

Then two operational layers measure and direct the gap between aspiration
and reality:

4. **`production-baseline.md`** — engineering bar. Audits reality against
   aspiration; lists gaps in priority order.
5. **`roadmap.md`** — phased build plan. Concrete, checkboxable.

## When something changes, update…

| Change | Doc to update |
|---|---|
| Product vision / scope shifts | `overview.md` |
| A core architectural principle | `system-architecture.md` (rare) |
| A feature ships, a bug is fixed, the running system works differently | `ARCHITECTURE.md` |
| An ops procedure changes | `OPS.md` |
| Engineering bar shifts, or you do an audit | `production-baseline.md` |
| Phase plan moves, an item ships, a new phase starts | `roadmap.md` (always) |

## Conventions

- **Markdown only.** No emojis. Tables and ASCII diagrams over prose
  where they fit.
- **Each doc has one job.** If you're tempted to add a section that
  belongs in another doc, link to it instead.
- **Link liberally between docs.** They're a graph, not a stack.
- **Prefer "why" over "what."** The codebase is the source of truth for
  *what*; docs explain the *why* and the *aspiration*.
- **Past decisions go in commit messages and PR descriptions.** Docs
  describe the current state and the direction, not the history.

## When the doc set itself needs to grow

Resist for as long as possible. Add a new doc only when an existing one
would have to grow a section that's *unrelated* to its main job.

Likely future additions, in order of probability:

- `decisions/` — lightweight ADRs, when a load-bearing decision needs more
  rationale than a commit message can carry.
- `agents/` — once there are 3+ agents, each gets its own doc explaining
  its inputs, outputs, prompts, and version history.
- `runbooks/` — once OPS.md grows past one page, split per-incident.
