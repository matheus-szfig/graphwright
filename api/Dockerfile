# Runs API image. Build context is the REPO ROOT (the engine is an editable path
# dependency — the contract, catalog and lint come from it):
#   docker build -f api/Dockerfile -t graphwright-api .
# No browser: the API never imports Playwright and no `playwright install` runs here
# (the Python package arrives as an engine dependency but stays unused).
# The same image runs the one-shot migration: `alembic upgrade head`.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/api/.venv/bin:$PATH" \
    GRAPHS_DIR=/app/engine/graphs

WORKDIR /app

# Engine first: it is the path dependency. Graphs ship with it (the catalog reads them).
COPY engine/pyproject.toml ./engine/
COPY engine/src ./engine/src
COPY engine/graphs ./engine/graphs
COPY api/pyproject.toml api/uv.lock ./api/

WORKDIR /app/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY api/src ./src
COPY api/alembic ./alembic
COPY api/alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000

CMD ["graphwright-api", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/api"]
