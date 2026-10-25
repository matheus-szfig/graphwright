# Dev-only graph builder API. Build context is the REPO ROOT (it needs engine/ as an
# editable path dependency):
#   docker build -f builder-api/Dockerfile -t graphwright-builder-api .
# Runs headless inside Docker; for a headed browser run it on the host instead
# (`make builder-api`) and point nginx at host.docker.internal:8765.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/builder-api/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    BUILDER_HOST=0.0.0.0 \
    BUILDER_HEADED=false \
    BUILDER_GRAPHS_DIR=/app/engine/graphs

WORKDIR /app

# engine first: it is the path dependency, and its lockfile pins Playwright.
COPY engine/pyproject.toml engine/uv.lock* ./engine/
COPY engine/src ./engine/src
COPY builder-api/pyproject.toml builder-api/uv.lock* ./builder-api/

WORKDIR /app/builder-api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    update-ca-certificates && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

COPY builder-api/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

EXPOSE 8765

CMD ["builder-api", "--headless", "--host", "0.0.0.0", "--port", "8765"]
