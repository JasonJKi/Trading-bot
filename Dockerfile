FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

COPY src ./src
COPY dashboard ./dashboard

RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV DATABASE_URL=sqlite:////app/data/trading.db

CMD ["python", "-m", "src.core.orchestrator"]
