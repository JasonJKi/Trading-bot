# Single container, single port. Two build stages:
#   1. node:  builds the Next.js dashboard as a static export (web/out/).
#   2. python: installs the worker + FastAPI, copies the prebuilt dashboard.
#
# At runtime the orchestrator runs in the background and uvicorn serves both
# /api/* (FastAPI) and / (the static dashboard) on a single port.

ARG PYTHON_VERSION=3.11
ARG NODE_VERSION=20

# ---------- web builder ----------
FROM node:${NODE_VERSION}-slim AS web-builder

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./
# Static export for production. NEXT_BUILD_MODE flips next.config.ts into
# `output: "export"` mode (see web/next.config.ts).
ENV NEXT_BUILD_MODE=export
RUN npm run build


# ---------- python builder ----------
FROM python:${PYTHON_VERSION}-slim AS py-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends curl \
        && rm -rf /var/lib/apt/lists/* \
        && useradd --create-home --shell /bin/bash app

WORKDIR /app

COPY --from=py-builder /opt/venv /opt/venv
COPY --chown=app:app src ./src
COPY --from=web-builder --chown=app:app /web/out ./web/out

RUN mkdir -p /app/data && chown -R app:app /app
VOLUME ["/app/data"]

USER app
ENV DATABASE_URL=sqlite:////app/data/trading.db
EXPOSE 8000

# Orchestrator runs in the background (it's the trading worker — no port).
# uvicorn is the foreground process; serves /api/* and the bundled dashboard.
CMD ["sh", "-c", "python -m src.core.orchestrator & exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000"]
