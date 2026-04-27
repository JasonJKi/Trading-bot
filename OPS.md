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
