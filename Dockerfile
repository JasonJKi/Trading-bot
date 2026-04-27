# Multi-stage build: builder produces the wheel, runtime is a slim image.
# The same image runs locally (docker compose) and in production (Fly).

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
COPY dashboard ./dashboard

# Install into a venv we can copy into the runtime image.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[dashboard]"


# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# curl is used by the docker-compose healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
        && rm -rf /var/lib/apt/lists/* \
        && useradd --create-home --shell /bin/bash app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app src ./src
COPY --chown=app:app dashboard ./dashboard
COPY --chown=app:app scripts ./scripts

RUN mkdir -p /app/data && chown -R app:app /app && chmod +x /app/scripts/run.sh
VOLUME ["/app/data"]

USER app
ENV DATABASE_URL=sqlite:////app/data/trading.db

EXPOSE 8080

CMD ["/app/scripts/run.sh"]
