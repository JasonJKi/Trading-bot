FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY dashboard ./dashboard
COPY scripts ./scripts

# Install base deps + dashboard extras (streamlit, plotly).
RUN pip install --upgrade pip && pip install ".[dashboard]"

RUN mkdir -p /app/data && chmod +x /app/scripts/run.sh
VOLUME ["/app/data"]

ENV DATABASE_URL=sqlite:////app/data/trading.db

EXPOSE 8080

# Runs orchestrator + Streamlit dashboard in parallel; shares /app/data.
CMD ["/app/scripts/run.sh"]
