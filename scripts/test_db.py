#!/usr/bin/env python
"""Run the api/ test suite including its database tests, against a throwaway
``graphwright_test`` database on the compose postgres (``make up-host-ports``).

The DB fixtures TRUNCATE tables, so they refuse any database whose name does not
end in ``_test``; this script creates that database if needed, migrates it and
runs pytest with ``POSTGRES_DSN`` pointing at it.

    uv run --no-project scripts/test_db.py [pytest args...]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DSN", "postgresql://graphwright:graphwright@127.0.0.1:5432/graphwright_test")


def sh(cmd: list[str], **kw) -> None:
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def main(argv: list[str]) -> int:
    exists = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "graphwright", "-d", "graphwright", "-tAc",
         "SELECT 1 FROM pg_database WHERE datname = 'graphwright_test'"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if exists.returncode != 0:
        print(exists.stderr or "is the stack up? (make up-host-ports)", file=sys.stderr)
        return exists.returncode
    if exists.stdout.strip() != "1":
        sh(["docker", "compose", "exec", "-T", "postgres", "createdb", "-U", "graphwright", "graphwright_test"], cwd=ROOT)

    env = {
        **os.environ,
        "POSTGRES_DSN": DSN,
        "ENV": "test",
        # Only meaningful for the throwaway database: the migration refuses the dev
        # default role password outside dev/test.
        "DB_PASSWORD_WORKER": os.environ.get("DB_PASSWORD_WORKER", "graphwright_worker"),
    }
    sh(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT / "api", env=env)
    return subprocess.call(["uv", "run", "pytest", *argv], cwd=ROOT / "api", env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
