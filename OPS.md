# Ops runbook

What to do when, in plain language. For the architectural picture see
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Daily / weekly checks

| Cadence | Check | How |
|---|---|---|
| Daily | Bot ran its cycle | Dashboard → Audit → look for `bot_error` / `broker_error` rows |
| Daily | Equity tracking | Dashboard → Overview → account equity tile |
| Weekly | DD per bot | Dashboard → per-bot tab → drawdown chart |
| Weekly | Backup integrity | `ls -lh data/backup/` then `gunzip -t data/backup/<latest>.gz` |
| Monthly | Secrets rotation | Regenerate Alpaca keys, rotate `DASHBOARD_PASSWORD`, update Fly secrets |

## Migrations

Schema changes ship as Alembic revisions. Workflow:

```bash
# 1. Edit a SQLAlchemy model in src/core/store.py.
# 2. Autogenerate a revision (review the diff before committing).
make db-revision MSG="add foo column to bar"
# → writes alembic/versions/<date>-<id>_add_foo_column_to_bar.py

# 3. Inspect the generated file. Edit if autogenerate missed something
#    (data migrations, constraint subtleties, etc.).

# 4. Apply locally to confirm.
make db-upgrade
make db-current   # should show the new revision id
```

In production, `make db-init` (which the Fly `release_command` runs and
which the launchd agents on the Mac mini call before bringing up the
worker) is the canonical bootstrap. It handles three database states:

- **Fresh DB** — alembic creates everything from zero.
- **Pre-alembic existing DB** — stamps at the initial revision (the
  schema already matches it), then runs any later migrations.
- **Already alembic-managed** — applies any unapplied revisions.

The pre-alembic path is a one-shot safety net for the existing Mac mini
deploy. After the first `db-init` post-upgrade, the DB is alembic-managed
and the path collapses to a normal upgrade.

Never edit migrations that have shipped — write a new revision. Never
edit `alembic_version` by hand unless you really know why.

## Deploying to the Mac mini

The Mac mini server runs three launchd agents (orchestrator + api +
cloudflared tunnel) — see [`ARCHITECTURE.md`](./ARCHITECTURE.md#mac-mini-layout)
for the process layout. Deploys are laptop-driven via the `mac-*` Make
targets; the server has no source-build toolchain.

| Step | Command | When |
|---|---|---|
| Bootstrap | `make mac-bootstrap` | once per server (installs `python@3.12`, `cloudflared`, creates venv, installs runtime deps from `deploy/requirements.txt`) |
| Push secrets | `make mac-env-push` | once + whenever `.env` changes (never auto-deployed) |
| Push tunnel creds | `make mac-tunnel-creds-push` | once after `cloudflared tunnel create` |
| Full deploy | `make mac-deploy` | every code change. Builds the dashboard locally, rsyncs source + prebuilt `web/out/`, regenerates and reloads launchd plists. |
| Restart only | `make mac-restart` | when you edited config on the server (e.g. `.env`) but no code changed |
| Stop both | `make mac-stop` | maintenance windows |
| Tail logs | `make mac-logs` | tails all four log streams (orchestrator + api stdout/err) in parallel |
| Status | `make mac-status` | quick health glance — `state = running` per agent |
| SSH in | `make mac-shell` | poke around manually |
| Preview a change | `make mac-deploy-preview` | builds dashboard locally, rsyncs only to `web-preview/out/`. Visible at `https://preview.67quant.com`. **No service restart, prod untouched.** |
| Promote preview | `make mac-promote` | server-side rsync `web-preview/out/` → `web/out/`. Atomic, no rebuild. Ships the exact bytes you saw on preview to `app./bot./67quant.com`. |

Override the host on any target: `make mac-deploy MAC_HOST=mac` for the LAN
path; the default is `mac-remote` (public SSH). Full setup including the
named-tunnel upgrade flow is in [`deploy/README.md`](./deploy/README.md).

### Preview / production split

Frontend-only preview lives on the same Mac mini, same uvicorn process,
different static tree:

```
~/Trading-bot/
├── web/out/             ← serves app./bot./67quant.com  (production)
└── web-preview/out/     ← serves preview.67quant.com    (preview)
```

`src/api/main.py`'s SPA fallback host-routes based on the request `Host:`
header. There's no second uvicorn, no second orchestrator, no second
tunnel — preview shares the same `/api/*` and the same DB, so you see
real data while iterating on the UI.

Iteration loop:

```bash
# 1. local edits → push to preview only
make mac-deploy-preview                # https://preview.67quant.com

# 2. happy with it → ship those exact bytes to production
make mac-promote                       # https://app.67quant.com

# (or back-out by deploying main fresh)
make mac-deploy                        # rebuilds + ships from local web/out/
```

`make mac-rsync` (used by `mac-deploy`) excludes `web-preview/` so a prod
deploy never clobbers an in-flight preview tree. Promotion is a server-
side `rsync -a --delete` between the two trees — no rebuild, no race.

**What this catches:** layout breakage, broken pages, fetch typos, brand
visual regressions — everything frontend-only.

**What this doesn't catch:** backend regressions. Both prod and preview
share the same FastAPI process; a buggy backend deploy hits both. For
backend changes, test locally first (`make run` or `make test`), then
`mac-deploy`. Most observed breakage has been frontend, so this is the
right cut today.

### Tunnel troubleshooting

| Symptom | First place to look |
|---|---|
| `502` from `https://app.67quant.com` | tunnel agent restarting — wait 5s; if persistent, `make mac-restart` |
| TLS handshake failure (`ssl/tls alert handshake failure`) | Cloudflare zone SSL/TLS mode is `Off` — set to `Full` in dashboard → SSL/TLS → Overview |
| Universal SSL `Authorizing` | brand-new subdomain, give it 5–15 min to issue |
| `Bootstrap failed: 5: Input/output error` from `services-install.sh` | known launchctl bootout/bootstrap race; the script already retries — re-run `make mac-services-install` and confirm with `make mac-status` |
| Tunnel agent up but URL `404` | hostname not in `~/.cloudflared/config.yml` ingress — edit [`deploy/cloudflared/config.yml.template`](./deploy/cloudflared/config.yml.template), scp the rendered config, `make mac-restart` |
| `cert.pem: file does not exist` on server | run `make mac-tunnel-creds-push` |

## Backups

- The orchestrator runs an automatic SQLite backup at **04:00 UTC daily**
  (`scripts/backup nightly` is wired into APScheduler).
- Manual backup: `make db-backup`.
- Backups are gzipped, retained 14 days, written to `data/backup/`.
- Restore: `gunzip data/backup/trading-YYYYMMDD-HHMMSS.db.gz` then move
  the result to `data/trading.db` (stop the orchestrator first).
- For off-host: cron a `rclone copy data/backup/ <remote>:trading-bot/`
  every morning. Or any S3/R2 client.

## Health endpoint

The orchestrator exposes `GET /healthz` on **0.0.0.0:8081** — JSON status,
200 if alive + DB reachable, 503 otherwise.

Use this with any uptime monitor (UptimeRobot, Healthchecks.io, your
homelab Prometheus, etc.).

```bash
curl -fsS http://localhost:8081/healthz
# {"status":"ok","ts":"2026-04-27T..."}
```

## Secrets

| Secret | Where set | Rotate |
|---|---|---|
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` | `.env` (local) or Fly secrets | Quarterly. Generate new pair in the Alpaca dashboard, redeploy, revoke old. |
| `DASHBOARD_PASSWORD` | `.env` or Fly secrets | Whenever someone leaves your trust circle. |
| `SESSION_SECRET` | `.env` or Fly secrets | Rotating it logs every dashboard user out. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Required for multi-worker deploys. |
| `SLACK_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL` | `.env` or Fly secrets | If a webhook URL leaks, revoke it in Slack/Discord, set a new one. |
| `SMTP_PASSWORD` | `.env` or Fly secrets | If your email provider rotates app passwords, update here. |

Rules of thumb:
- Never commit `.env`. It's in `.gitignore`.
- Never paste a key into a chat / GitHub issue / Slack channel.
- For Fly: `fly secrets set` always (never edit `fly.toml` for secrets).

## Going live (recap)

1. ≥ 30 days of paper data per bot.
2. `python -m src.cli graduate --strategy <id>` per bot. Refuses if metrics fail.
3. Set `ALPACA_PAPER=false` AND `ALPACA_LIVE_CONFIRM=YES_I_MEAN_IT`.
4. Restart. Orchestrator double-checks every enabled bot is graduated.
5. Watch `audit_events` like a hawk for the first week.

## Common operations

```bash
# See bot state at a glance
python -m src.cli status

# Pause a bot (e.g. earnings season for one of its names)
python -m src.cli pause --strategy momentum --reason "earnings week"

# Resume
python -m src.cli enable --strategy momentum

# Manual backup
make db-backup

# Run all bots once right now (useful after a config change)
make run-once

# Tail logs (local)
docker compose logs -f worker

# Tail logs (Fly)
make fly-logs

# Deploy a code change to the Mac mini
make mac-deploy

# Tail orchestrator + api + tunnel logs on the Mac mini
make mac-logs

# Reload agents on the Mac mini after editing .env on the server
make mac-restart
```

## Disaster recovery

1. **Lost the data volume.** Restore the most recent backup:
   ```
   stop the orchestrator
   gunzip -c data/backup/trading-LATEST.db.gz > data/trading.db
   restart
   ```
2. **Alpaca credentials compromised.** Revoke at app.alpaca.markets,
   generate new pair, `fly secrets set ALPACA_API_KEY=... ALPACA_API_SECRET=...`,
   redeploy.
3. **Bot misbehaving.** `python -m src.cli pause --strategy <id>` first.
   Diagnose via the Audit tab + Trades + Orders. Don't redeploy under
   pressure; observe and write a fix offline.
