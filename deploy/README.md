# Deploy to a Mac mini server

Build on your laptop, ship to a remote Mac, manage via `make`. The server has
no source-build toolchain — it gets a prebuilt static dashboard, a Python
venv with declared deps, and two launchd agents (orchestrator + api).

## Prerequisites on the server (one-time, manual)

1. **SSH access.** Add an entry to your laptop's `~/.ssh/config`, e.g.:
   ```
   Host mac
     HostName 192.168.x.x
     User jason
     IdentityFile ~/.ssh/id_rsa_jason
   ```
2. **Auto-login enabled** (System Settings → Users & Groups → Automatically
   log in as). Required for LaunchAgents to come up after a reboot.
3. **Homebrew installed.** If `brew --version` fails, install it from
   https://brew.sh and add to PATH.

That's it. Everything else is automated by `make mac-bootstrap`.

## First-time setup

From your laptop:

```bash
make mac-bootstrap     # installs python@3.12 + cloudflared via brew on server,
                       # creates ~/Trading-bot, ~/Trading-bot/.venv, installs deps
make mac-env-push      # one-time: scp your local .env to the server
make mac-deploy        # builds dashboard locally, rsyncs source, installs/loads agents
make mac-status        # confirm orchestrator + api are running
```

The dashboard is now reachable on the server's LAN at
`http://<server-ip>:<API_PORT>` (default `:8000`).

## Day-to-day

```bash
make mac-deploy        # ship local changes (rebuilds dashboard, rsyncs, restarts)
make mac-restart       # just restart the agents (no code change)
make mac-logs          # tail orchestrator + api logs
make mac-status        # launchctl print-state
make mac-shell         # SSH into the server (interactive)
```

Override the SSH host (e.g. when away from home):

```bash
make mac-deploy MAC_HOST=mac-remote
```

## Public access via Cloudflare Tunnel

```bash
make mac-tunnel        # runs `cloudflared tunnel --url http://localhost:$API_PORT`
                       # in foreground over ssh; Ctrl-C stops it
```

This Quick Tunnel gives you a free `*.trycloudflare.com` URL but it changes
every time `cloudflared` restarts. For a stable URL, use a named tunnel —
see "Named tunnel" below.

### Named tunnel (stable URL, requires Cloudflare account + domain)

Run on the server, once:
```bash
ssh mac
cloudflared tunnel login                       # opens a browser to auth
cloudflared tunnel create trading-bot
cloudflared tunnel route dns trading-bot bot.<your-domain>
cloudflared tunnel run trading-bot --url http://localhost:8000
```
Then promote it to a launchd-managed service so it survives reboots; the
shape mirrors the api/orchestrator plists.

## Files

```
deploy/
├── README.md                              # this file
├── requirements.txt                       # pinned runtime deps (server pip install)
├── services-install.sh                    # runs on server: install + load agents
├── services-uninstall.sh                  # runs on server: unload + remove agents
└── launchd/
    ├── com.tradingbot.orchestrator.plist  # template, __PLACEHOLDERS__
    └── com.tradingbot.api.plist           # template, __PLACEHOLDERS__
```

## What `make mac-deploy` actually does

1. **Locally:** `cd web && NEXT_BUILD_MODE=export npm run build` →
   produces `web/out/`.
2. **rsync** to server, with these files explicitly excluded so they don't
   get clobbered:
   - `.env`           (server's secrets — push manually with `mac-env-push`)
   - `data/`          (live SQLite — never overwrite)
   - `logs/`
   - `.venv/`         (built on server, has different paths)
   - `node_modules/`, `.next/`, `__pycache__/`, `.git/`
3. **On server:** run `deploy/services-install.sh` to refresh + reload
   agents (`launchctl bootout` → `bootstrap`). New code is now live.

## Switching to PostgreSQL or live trading later

Both are env-var changes only:
- Edit `.env` on the server (`ssh mac` then `nano ~/Trading-bot/.env`),
- `make mac-restart`.

For live trading you'll also need to set `ALPACA_LIVE_CONFIRM=YES_I_MEAN_IT`
and have all bots paper-validated — see the project root README.
