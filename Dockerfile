FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first (separate layer) so source-only changes don't reinstall everything.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

# Copy source after deps so this layer churns without rebuilding the heavy one above.
COPY src ./src
COPY dashboard ./dashboard

# Re-install in editable mode now that source is present (cheap — deps are cached).
RUN pip install -e .

RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV DATABASE_URL=sqlite:////app/data/trading.db

# Initialize DB schema before each deploy goes live.
# `fly.toml` invokes this via `release_command`.
CMD ["python", "-m", "src.core.orchestrator"]
