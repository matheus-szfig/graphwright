# Worker image: the graph engine + a stealth Playwright Chromium, run by Celery.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Resolve deps first (cache-friendly layer).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Playwright Chromium + system libraries, in their own layer so source changes
# don't re-download the browser. Invoke the binary directly (not `uv run`): the
# project isn't installed yet at this layer.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    update-ca-certificates && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

# Graphs are data the worker runs (graphwright/catalog.py), so they ship with it.
COPY src/ ./src/
COPY graphs/ ./graphs/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Unprivileged at runtime (the browser binaries under /ms-playwright stay root-owned, read-only).
RUN useradd --create-home --uid 10001 worker && chown -R worker /app
USER worker

CMD ["celery", "-A", "graphwright.queue.app", "worker", "--queues=graphwright.runs,graphwright.maintenance", \
     "--concurrency=1", "--max-tasks-per-child=10", "--loglevel=info"]
