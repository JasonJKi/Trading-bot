# Convenience targets for local + dockerized development.
# Run `make help` to see them all.

PY ?= python
PIP ?= pip
APP ?= trading-bot-cpg3lw

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: install
install:  ## Install all deps (base + dashboard + dev) for local development.
	$(PIP) install -e ".[dev]"

.PHONY: install-base
install-base:  ## Install only base deps (matches the Fly worker image).
	$(PIP) install -e .

.PHONY: test
test:  ## Run the test suite.
	$(PY) -m pytest -q

.PHONY: test-cov
test-cov:  ## Run tests with coverage.
	$(PY) -m pytest --cov=src --cov-report=term-missing

.PHONY: lint
lint:  ## Lint with ruff.
	$(PY) -m ruff check src tests

.PHONY: fmt
fmt:  ## Auto-format with ruff.
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

.PHONY: run-once
run-once:  ## Run all enabled bots once and exit.
	$(PY) -m src.core.orchestrator --once

.PHONY: run
run:  ## Run worker + API + Next.js dashboard (full stack).
	./scripts/run.sh

.PHONY: api
api:  ## Run only the FastAPI dashboard backend (port from .env API_PORT, default 8000).
	$(PY) -m uvicorn src.api.main:app --reload --port $${API_PORT:-8000}

.PHONY: web
web:  ## Run only the Next.js dashboard dev server (port from .env WEB_PORT, default 3000).
	cd web && npm run dev -- --port $${WEB_PORT:-3000}

.PHONY: backtest
backtest:  ## Backtest a strategy. Usage: make backtest STRAT=momentum START=2024-01-01 END=2025-12-31
	$(PY) -m src.backtest.runner --strategy $(STRAT) --start $(START) --end $(END)

.PHONY: db-init
db-init:  ## Initialize the SQLite schema.
	$(PY) -m src.core.init_db

.PHONY: db-backup
db-backup:  ## Snapshot the SQLite DB (online, gzipped) to data/backup/.
	$(PY) -m src.core.backup

.PHONY: healthz
healthz:  ## Probe the local /healthz endpoint.
	curl -sf http://localhost:8081/healthz && echo

# ---------- docker-compose ----------
.PHONY: up
up:  ## Bring up the local stack (sqlite by default).
	docker compose up --build

.PHONY: up-pg
up-pg:  ## Bring up the local stack with Postgres instead of SQLite.
	docker compose --profile postgres up --build

.PHONY: down
down:  ## Stop the local stack.
	docker compose down

.PHONY: logs
logs:  ## Tail logs from the local stack.
	docker compose logs -f

.PHONY: shell
shell:  ## Open a shell in the running worker container.
	docker compose exec worker bash

# ---------- fly.io ----------
.PHONY: fly-deploy
fly-deploy:  ## Deploy to Fly (skips if no flyctl).
	fly deploy -a $(APP)

.PHONY: fly-logs
fly-logs:  ## Tail Fly logs.
	fly logs -a $(APP)

.PHONY: fly-ssh
fly-ssh:  ## SSH into the Fly machine.
	fly ssh console -a $(APP)

.PHONY: fly-secrets
fly-secrets:  ## List configured Fly secrets.
	fly secrets list -a $(APP)

# ---------- remote Mac mini server ----------
# Drives a remote macOS host via ssh + rsync. See deploy/README.md.
#
# Override the host on the command line, e.g. when away from home:
#   make mac-deploy MAC_HOST=mac-remote
MAC_HOST     ?= mac
MAC_APP_DIR  ?= /Users/jason/Trading-bot
MAC_LABELS   := com.tradingbot.orchestrator com.tradingbot.api

# rsync excludes: never ship the venv, dev caches, or the server's runtime state.
# `.env` is excluded so a stale local .env never overwrites the server's secrets;
# use `make mac-env-push` explicitly when you mean to.
MAC_RSYNC_EXCLUDES := \
	--exclude=.git/ \
	--exclude=.venv/ \
	--exclude=node_modules/ \
	--exclude=.next/ \
	--exclude=__pycache__/ \
	--exclude='*.pyc' \
	--exclude=.pytest_cache/ \
	--exclude=.ruff_cache/ \
	--exclude=.DS_Store \
	--exclude=.env \
	--exclude=.env.local \
	--exclude=data/ \
	--exclude=logs/

.PHONY: mac-bootstrap
mac-bootstrap:  ## One-time: brew packages, app dir, venv, deps on $(MAC_HOST).
	@echo ">> bootstrapping $(MAC_HOST):$(MAC_APP_DIR)"
	ssh $(MAC_HOST) ' \
		set -e; \
		eval "$$(/opt/homebrew/bin/brew shellenv)"; \
		brew list python@3.12 >/dev/null 2>&1 || brew install python@3.12; \
		brew list cloudflared >/dev/null 2>&1 || brew install cloudflared; \
		mkdir -p $(MAC_APP_DIR)/data $(MAC_APP_DIR)/logs $(MAC_APP_DIR)/deploy/launchd; \
		[ -d $(MAC_APP_DIR)/.venv ] || python3.12 -m venv $(MAC_APP_DIR)/.venv; \
		$(MAC_APP_DIR)/.venv/bin/pip install --upgrade pip wheel >/dev/null; \
	'
	rsync -avz deploy/ $(MAC_HOST):$(MAC_APP_DIR)/deploy/
	ssh $(MAC_HOST) '$(MAC_APP_DIR)/.venv/bin/pip install -r $(MAC_APP_DIR)/deploy/requirements.txt'
	@echo ">> bootstrap complete. next: make mac-env-push && make mac-deploy"

.PHONY: mac-env-push
mac-env-push:  ## scp local .env to $(MAC_HOST) (one-time / when secrets change).
	@test -f .env || (echo "no local .env to push" >&2; exit 1)
	scp .env $(MAC_HOST):$(MAC_APP_DIR)/.env
	ssh $(MAC_HOST) 'chmod 600 $(MAC_APP_DIR)/.env'
	@echo ">> .env pushed (mode 600)"

.PHONY: mac-build
mac-build:  ## Build the Next.js dashboard locally as a static export.
	@test -d web/node_modules || (cd web && npm install --silent)
	cd web && NEXT_BUILD_MODE=export npm run build

.PHONY: mac-rsync
mac-rsync: mac-build  ## rsync local source + prebuilt web/out to $(MAC_HOST).
	rsync -avz --delete-after $(MAC_RSYNC_EXCLUDES) ./ $(MAC_HOST):$(MAC_APP_DIR)/

.PHONY: mac-services-install
mac-services-install:  ## (Re)install + reload LaunchAgents on $(MAC_HOST).
	ssh $(MAC_HOST) 'bash $(MAC_APP_DIR)/deploy/services-install.sh'

.PHONY: mac-services-uninstall
mac-services-uninstall:  ## Unload + remove LaunchAgents on $(MAC_HOST).
	ssh $(MAC_HOST) 'bash $(MAC_APP_DIR)/deploy/services-uninstall.sh'

.PHONY: mac-deploy
mac-deploy: mac-rsync mac-services-install  ## Build + rsync + (re)install agents — full deploy.
	@echo ">> deployed to $(MAC_HOST). check status with: make mac-status"

.PHONY: mac-restart
mac-restart:  ## Restart both agents on $(MAC_HOST) (no code change).
	@for label in $(MAC_LABELS); do \
		ssh $(MAC_HOST) "launchctl kickstart -k gui/\$$(id -u)/$$label" || true; \
		echo "restarted: $$label"; \
	done

.PHONY: mac-stop
mac-stop:  ## Stop both agents on $(MAC_HOST) until next reload/reboot.
	@for label in $(MAC_LABELS); do \
		ssh $(MAC_HOST) "launchctl bootout gui/\$$(id -u)/$$label" || true; \
		echo "stopped: $$label"; \
	done

.PHONY: mac-status
mac-status:  ## Show agent state on $(MAC_HOST).
	@for label in $(MAC_LABELS); do \
		echo "=== $$label ==="; \
		ssh $(MAC_HOST) "launchctl print gui/\$$(id -u)/$$label 2>&1 | head -20" || true; \
	done

.PHONY: mac-logs
mac-logs:  ## Tail orchestrator + api logs on $(MAC_HOST).
	ssh $(MAC_HOST) 'tail -F $(MAC_APP_DIR)/logs/orchestrator.out.log $(MAC_APP_DIR)/logs/orchestrator.err.log $(MAC_APP_DIR)/logs/api.out.log $(MAC_APP_DIR)/logs/api.err.log'

.PHONY: mac-tunnel
mac-tunnel:  ## Open a Cloudflare Quick Tunnel on $(MAC_HOST). Ctrl-C stops it.
	@port=$$(ssh $(MAC_HOST) "grep -E '^API_PORT=' $(MAC_APP_DIR)/.env 2>/dev/null | cut -d= -f2"); \
	port=$${port:-8000}; \
	echo ">> opening Quick Tunnel to http://localhost:$$port on $(MAC_HOST)"; \
	ssh -t $(MAC_HOST) "cloudflared tunnel --url http://localhost:$$port"

.PHONY: mac-shell
mac-shell:  ## Open an interactive shell on $(MAC_HOST).
	ssh -t $(MAC_HOST) 'cd $(MAC_APP_DIR) && exec $$SHELL -l'
