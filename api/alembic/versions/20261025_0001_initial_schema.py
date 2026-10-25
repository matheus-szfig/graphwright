"""Initial schema: run, run_event, artifact, and the worker's role.

Revision ID: 0001
Revises:
Create Date: 2026-10-25

``run`` is written by the API (create, cancel) and by the worker (claim,
progress, outcome). Column names are the contract with
``engine/src/graphwright/storage/db.py`` — change both together.

The worker connects as ``graphwright_worker``: SELECT on ``run`` and UPDATE on
exactly the columns it writes; INSERT on ``run_event`` / ``artifact``. It cannot
create or delete runs, or rewrite what a caller asked for (graph, inputs,
credential reference). Its password comes from DB_PASSWORD_WORKER at migrate
time; outside ENV=dev/test the migration refuses to fall back to the default.

UUIDs are Postgres 18's native ``uuidv7()`` — time-ordered, so ``ORDER BY id``
is creation order and keyset pagination needs no extra index.
"""

from __future__ import annotations

from alembic import op
from graphwright_api.config import settings

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

WORKER_ROLE = "graphwright_worker"

# Every column claim_run / update_run_progress / mark_run_* / set_run_graph_version touch.
WORKER_RUN_COLUMNS = (
    "status", "started_at", "attempt", "celery_task_id", "worker_hostname", "worker_pid",
    "browser_profile", "graph_version", "steps_completed", "current_node", "finished_at",
    "result", "terminal", "failure", "error_type", "error_message", "error_traceback",
)


def _worker_password() -> str:
    password = settings.db_password_worker
    if not password:
        if not settings.is_dev:
            raise RuntimeError(f"DB_PASSWORD_WORKER must be set to create role {WORKER_ROLE} outside dev/test")
        password = WORKER_ROLE
    return password.replace("'", "''")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run (
            id                  uuid PRIMARY KEY DEFAULT uuidv7(),
            graph               text NOT NULL,
            graph_version       integer,
            status              text NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
            inputs              jsonb NOT NULL DEFAULT '{}'::jsonb,
            credential_ref      text,
            requested_by        text,
            idempotency_key     text,
            attempt             integer NOT NULL DEFAULT 0,
            celery_task_id      text,
            worker_hostname     text,
            worker_pid          integer,
            browser_profile     text,
            current_node        text,
            steps_completed     integer NOT NULL DEFAULT 0,
            terminal            text,
            result              jsonb,
            failure             jsonb,
            error_type          text,
            error_message       text,
            error_traceback     text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            started_at          timestamptz,
            finished_at         timestamptz,
            cancel_requested_at timestamptz
        );
        """
    )
    # One idempotency key per caller; NULLs (no key) never collide.
    op.execute("CREATE UNIQUE INDEX run_idempotency_uq ON run (requested_by, idempotency_key) WHERE idempotency_key IS NOT NULL;")
    op.execute("CREATE INDEX run_status_created_idx ON run (status, created_at);")
    op.execute("CREATE INDEX run_graph_created_idx ON run (graph, created_at);")

    op.execute(
        """
        CREATE TABLE run_event (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id      uuid NOT NULL REFERENCES run (id) ON DELETE CASCADE,
            seq         integer,
            kind        text NOT NULL,
            node        text,
            data        jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX run_event_run_idx ON run_event (run_id, id);")

    op.execute(
        """
        CREATE TABLE artifact (
            id            uuid PRIMARY KEY DEFAULT uuidv7(),
            run_id        uuid NOT NULL REFERENCES run (id) ON DELETE CASCADE,
            kind          text NOT NULL,
            bucket        text NOT NULL,
            object_key    text NOT NULL,
            content_type  text,
            size_bytes    bigint,
            node          text,
            metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at    timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX artifact_run_idx ON artifact (run_id);")

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
                CREATE ROLE {WORKER_ROLE} LOGIN PASSWORD '{_worker_password()}';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {WORKER_ROLE};")
    op.execute(f"GRANT SELECT ON run TO {WORKER_ROLE};")
    op.execute(f"GRANT UPDATE ({', '.join(WORKER_RUN_COLUMNS)}) ON run TO {WORKER_ROLE};")
    # insert_artifact uses RETURNING id, which needs SELECT.
    op.execute(f"GRANT SELECT, INSERT ON artifact TO {WORKER_ROLE};")
    op.execute(f"GRANT INSERT ON run_event TO {WORKER_ROLE};")
    op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {WORKER_ROLE};")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS artifact;")
    op.execute("DROP TABLE IF EXISTS run_event;")
    op.execute("DROP TABLE IF EXISTS run;")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
                REVOKE ALL ON SCHEMA public FROM {WORKER_ROLE};
                DROP ROLE {WORKER_ROLE};
            END IF;
        END
        $$;
        """
    )
