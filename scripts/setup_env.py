#!/usr/bin/env python
"""Copy every .env*.example in the repo to its live sibling.

App templates (``engine/.env.example``, ``api/.env.example``) → ``.env.docker``
(default, read by docker compose) or ``.env`` (``--local``, hostnames rewritten
to the loopback ports docker-compose.host-ports.yml publishes). The repo-root
``.env.example`` is compose's own variable file and always becomes ``.env``.

Existing targets are left alone unless ``--force`` (no backup: hand-edits are lost).

    uv run --no-project scripts/setup_env.py               # .env.docker
    uv run --no-project scripts/setup_env.py --local       # .env (host runs)
    uv run --no-project scripts/setup_env.py --local --force

Usually invoked through `make setup-env` / `make setup-env-local` (+ `-force`).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", "dist"}
EXAMPLE_SUFFIX = ".example"

# Templates target the compose network; host runs use the loopback ports.
LOCAL_REWRITES = [
    ("@postgres:5432", "@127.0.0.1:5432"),
    ("redis://redis:6379", "redis://127.0.0.1:6379"),
    ("http://minio:9000", "http://127.0.0.1:9900"),
    ("http://demo-site:8090", "http://127.0.0.1:8090"),
]


def find_examples() -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.startswith(".env") and name.endswith(EXAMPLE_SUFFIX):
                found.append(Path(dirpath) / name)
    return sorted(found)


def target_name(example: Path, local: bool) -> str:
    stem = example.name[: -len(EXAMPLE_SUFFIX)].rstrip(".")  # ".env" or ".env.x"
    if example.parent == ROOT:
        return stem  # compose reads the root .env as-is
    return stem if local else f"{stem}.docker"


def render(example: Path, dest: Path, local: bool) -> None:
    text = example.read_text(encoding="utf-8", newline="")
    if local and example.parent != ROOT:
        for old, new in LOCAL_REWRITES:
            text = text.replace(old, new)
    dest.write_text(text, encoding="utf-8", newline="")


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy every .env.example to .env.docker (default) or .env (--local).")
    parser.add_argument("-l", "--local", action="store_true", help="write .env with loopback hosts, for host runs")
    parser.add_argument("-f", "--force", action="store_true", help="overwrite existing files (hand-edits are lost)")
    args = parser.parse_args()

    examples = find_examples()
    if not examples:
        print(f"no .env*.example files found under {ROOT}")
        return 0

    label = "host runs (.env)" if args.local else "docker compose (.env.docker)"
    print(f"Setting up env files for {label}{' - FORCE: overwriting' if args.force else ''}...")
    dests = [ex.with_name(target_name(ex, args.local)) for ex in examples]
    for ex, dest in zip(examples, dests, strict=True):
        shown = dest.relative_to(ROOT).as_posix()
        if dest.exists() and not args.force:
            print(f"  exists    {shown}")
            continue
        verb = "overwrote" if dest.exists() else "created  "
        render(ex, dest, args.local)
        print(f"  {verb} {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
