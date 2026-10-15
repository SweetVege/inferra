# Build from the repository root: docker build -f services/api/Dockerfile .
FROM python:3.12-slim

# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    INFERRA_SAMPLES_DIR=/app/samples INFERRA_DATA_DIR=/data

WORKDIR /app/services/api
COPY services/api/pyproject.toml services/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/api ./
COPY samples /app/samples
RUN uv sync --frozen --no-dev

EXPOSE 8000
# Migrate, make sure the demo workspace exists, then serve. The worker can run in-process
# (INFERRA_INLINE_WORKER=true) or as a separate service with: uv run python -m inferra.worker
CMD ["sh", "-c", "uv run alembic upgrade head && uv run python -m inferra.seed && uv run uvicorn inferra.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
