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
	$(PY) -m ruff check src tests dashboard

.PHONY: fmt
fmt:  ## Auto-format with ruff.
	$(PY) -m ruff format src tests dashboard
	$(PY) -m ruff check --fix src tests dashboard

.PHONY: run-once
run-once:  ## Run all enabled bots once and exit.
	$(PY) -m src.core.orchestrator --once

.PHONY: run
run:  ## Run the orchestrator (long-running scheduler).
	$(PY) -m src.core.orchestrator

.PHONY: dashboard
dashboard:  ## Run the Streamlit dashboard locally.
	$(PY) -m streamlit run dashboard/app.py

.PHONY: backtest
backtest:  ## Backtest a strategy. Usage: make backtest STRAT=momentum START=2024-01-01 END=2025-12-31
	$(PY) -m src.backtest.runner --strategy $(STRAT) --start $(START) --end $(END)

.PHONY: db-init
db-init:  ## Initialize the SQLite schema.
	$(PY) -m src.core.init_db

.PHONY: db-backup
db-backup:  ## Snapshot the SQLite DB to data/backup/.
	mkdir -p data/backup
	cp data/trading.db data/backup/trading-$$(date +%Y%m%d-%H%M%S).db
	@echo "Backed up data/trading.db -> data/backup/"

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
