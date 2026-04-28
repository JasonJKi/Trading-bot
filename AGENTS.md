# AGENTS.md

Living documentation for this repository, written for AI agents and human
contributors who pick up the project mid-stream and need orientation.

## Convention

Each `AGENTS.md` documents the area it sits in. Update the relevant
`AGENTS.md` in the same change that materially reshapes its area —
brand, architecture, conventions, the stack, or the high-level shape.
Do not update an `AGENTS.md` for routine bug fixes or small refactors;
do update it when a future reader would otherwise be misled.

If a single change spans multiple areas, update each area's file.

## Index

- [`AGENTS.md`](./AGENTS.md) — repo-wide. The brand, the layout, the docs
  policy. (this file)
- [`web/AGENTS.md`](./web/AGENTS.md) — frontend stack and the **67quant**
  branding system (wordmark, palette, voice).

Add new entries as new `AGENTS.md` files appear. Keep this index short —
one line per file, ordered by area.

## The product

**67quant** (domain: `67quant.com`) is a multi-strategy paper-first
algorithmic trading platform with an AI research layer. Engineering
detail lives in [`docs/overview.md`](./docs/overview.md) and
[`docs/system-architecture.md`](./docs/system-architecture.md); this
file does not duplicate them.

## Brand rationale

The `67` reference is to the 2025 Gen-Alpha meme that became
Dictionary.com's Word of the Year. It originates from Skrilla's drill
song *"Doot Doot (6 7)"* and the alternating-palms hand gesture
("maybe up, maybe down" / "so-so") that spread with it.

We use it ironically. The literal meaning of the gesture — *probably
this, probably that* — is the exact metaphor for probabilistic trading.
That is the whole joke and the whole brand.

**Hard rules:**

1. **Never explain the meme on the site.** The joke dies on contact
   with explanation. No "About 67quant" section that mentions TikTok,
   Skrilla, LaMelo Ball, or hand gestures.
2. **Never use the 🤲 emoji** in commits, code, or copy unless the user
   explicitly asks. Same logic — over-signalling kills it.
3. **Sound like a quant tool, not a meme account.** Real backtests,
   real Sharpe ratios, monospace numerals. The brand pulls weight by
   being technically credible, with one ironic line at most per page.
4. **The wordmark is the logo.** Don't add a second logo, don't render
   "67quant" as plain text where the wordmark would fit. See
   [`web/AGENTS.md`](./web/AGENTS.md) for the component.

## Repository layout

```
src/             trading core, research pipeline, FastAPI backend
web/             Next.js dashboard — the brand surface
docs/            human-facing engineering documentation
deploy/          laptop → Mac mini deploy automation
alembic/         database migrations
tests/           pytest suite (no network required)
```

## Hosting topology and URLs

The product runs on a Mac mini home server (always on). Three launchd
LaunchAgents handle orchestration, the read-only API, and a Cloudflare
named tunnel. Deploys are driven from the laptop via `make mac-*` targets;
the server has no source-build toolchain. Operating manual:
[`ARCHITECTURE.md`](./ARCHITECTURE.md#deployment-topology). Runbook:
[`OPS.md`](./OPS.md#deploying-to-the-mac-mini). Workflow:
[`deploy/README.md`](./deploy/README.md).

The public URL surface (Phase 24 in [`docs/roadmap.md`](./docs/roadmap.md)):

| Hostname | Audience | Auth |
|---|---|---|
| `67quant.com` | marketing landing (future) | public |
| `app.67quant.com` | full operator dashboard | password-gated |
| `bot.67quant.com` | public per-bot tear sheets (under construction) | public, read-only |

## When to update which file

| Change                                              | Update                                    |
| --------------------------------------------------- | ----------------------------------------- |
| Brand visual / wordmark / palette / voice           | [`web/AGENTS.md`](./web/AGENTS.md)        |
| New top-level area added (e.g. `mobile/`)           | this file (index) + new `AGENTS.md` there |
| New documentation convention                        | this file                                 |
| New trading strategy / backend module               | [`docs/`](./docs/) — not `AGENTS.md`      |
| New frontend route / component pattern              | [`web/AGENTS.md`](./web/AGENTS.md)        |
| Deployment topology / launchd / tunnel ingress      | [`ARCHITECTURE.md`](./ARCHITECTURE.md) + [`OPS.md`](./OPS.md) |
| Ops procedure (deploy, restart, troubleshoot)       | [`OPS.md`](./OPS.md)                      |
| New URL / hostname in the public surface            | this file (the URL table) + [`docs/roadmap.md`](./docs/roadmap.md) |
| Bug fix, small refactor, dependency bump            | nothing                                   |
