# Worker + FastAPI image. The Next.js dashboard ships separately
# (Vercel, Netlify, Fly's static sites, etc) — see web/ for that.
#
# Multi-stage build: builder produces the venv, runtime is slim.

ARG PYTHON_VERSION=3.11

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

# Base deps include FastAPI + uvicorn now, so no extras needed.
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

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app src ./src

RUN mkdir -p /app/data && chown -R app:app /app
VOLUME ["/app/data"]

USER app
ENV DATABASE_URL=sqlite:////app/data/trading.db

EXPOSE 8000

# Run orchestrator + FastAPI in one container. The orchestrator runs in the
# background; uvicorn is the foreground process.
CMD ["sh", "-c", "python -m src.core.orchestrator & exec uvicorn src.api.main:app --host 0.0.0.0 --port 8000"]
