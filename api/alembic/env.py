"""Alembic environment — points at settings.postgres_dsn (the owner role).

The runs API is the single source of truth for the schema: ``run``,
``run_event``, ``artifact`` and the worker's least-privilege role. Migrations
are raw SQL via ``op.execute(...)``; there is no ORM metadata to autogenerate from.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from graphwright_api.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sqlalchemy_url(dsn: str) -> str:
    # POSTGRES_DSN is a plain libpq URL (what asyncpg takes); Alembic needs a dialect.
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn[len(prefix):]
    return dsn


config.set_main_option("sqlalchemy.url", _sqlalchemy_url(settings.postgres_dsn).replace("%", "%%"))
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
