#!/usr/bin/env python
"""Run the whole graph builder locally with a HEADED browser.

The API (uv) and the UI (Vite) run side by side in one terminal; Ctrl-C stops both.
Dependencies are installed on first run (uv sync, npm ci, Playwright Chromium).

Examples
--------
    make builder:local            # http://localhost:5174/builder/
    make builder:local NGINX=1    # also re-point the stack's nginx at these two, so
                                  # http://localhost:8080/builder/ works while this runs

    uv run --no-project builder-api/scripts/dev_local.py [--nginx] [--api-port N] [--web-port N]

Env overrides: API_PORT (8765), WEB_PORT (5174), NGINX (0|1), HTTP_PORT (8080).
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
API_DIR = ROOT / "builder-api"
WEB_DIR = ROOT / "builder-web"
WINDOWS = os.name == "nt"


def say(msg: str) -> None:
    prefix = "\033[36m[builder]\033[0m" if sys.stdout.isatty() else "[builder]"
    print(f"{prefix} {msg}", flush=True)


def exe(name: str) -> str:
    # Resolves npm -> npm.cmd on Windows, which subprocess won't do by itself.
    path = shutil.which(name)
    if path is None:
        sys.exit(f"[builder] '{name}' not found on PATH")
    return path


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, quiet: bool = False) -> int:
    out = subprocess.DEVNULL if quiet else None
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=out).returncode


def listening(port: int, path: str) -> bool:
    # Any HTTP answer counts, even an error status: something is serving the port.
    try:
        urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=1)
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False
    return True


def install_deps() -> None:
    if not (API_DIR / ".venv").is_dir():
        say("installing builder-api deps (uv sync)")
        if run([exe("uv"), "sync"], API_DIR):
            sys.exit("[builder] uv sync failed")
    # No-op when the matching Chromium build is already in the Playwright cache.
    if run([exe("uv"), "run", "playwright", "install", "chromium"], API_DIR, quiet=True):
        say("warning: playwright install chromium failed - the API will tell you if a browser is missing")

    if not (WEB_DIR / "node_modules").is_dir():
        say("installing builder-web deps (npm ci)")
        if run([exe("npm"), "ci", "--no-audit", "--no-fund"], WEB_DIR):
            sys.exit("[builder] npm ci failed")


def point_nginx_here(api_port: int, web_port: int) -> None:
    http_port = os.environ.get("HTTP_PORT", "8080")
    say(f"re-pointing nginx -> host (API :{api_port}, UI :{web_port}) at http://localhost:{http_port}/builder/")
    env = {
        **os.environ,
        "BUILDER_API_UPSTREAM": f"host.docker.internal:{api_port}",
        "BUILDER_WEB_UPSTREAM": f"host.docker.internal:{web_port}",
    }
    if run([exe("docker"), "compose", "up", "-d", "nginx"], ROOT, env=env):
        sys.exit("[builder] docker compose up -d nginx failed")
    say("note: the next plain 'docker compose up -d nginx' reverts nginx to the container upstreams")


def start(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.Popen:
    # On POSIX each child gets its own process group so stop() can take down the whole
    # tree (uv -> python, npm -> node). On Windows taskkill /T does that instead.
    return subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=not WINDOWS)


def stop(procs: list[subprocess.Popen]) -> None:
    say("stopping")
    for p in procs:
        if p.poll() is not None:
            continue
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the graph builder API + UI locally (headed).")
    parser.add_argument("--api-port", type=int, default=int(os.environ.get("API_PORT", "8765")))
    parser.add_argument("--web-port", type=int, default=int(os.environ.get("WEB_PORT", "5174")))
    parser.add_argument("--nginx", action="store_true", default=os.environ.get("NGINX", "0") == "1",
                        help="also route the stack's nginx (:8080/builder/) to these two")
    args = parser.parse_args()
    api_port, web_port = args.api_port, args.web_port

    install_deps()
    if args.nginx:
        point_nginx_here(api_port, web_port)

    api_running = listening(api_port, "/health")
    web_running = listening(web_port, "/builder/")
    if api_running:
        say(f"API already running on :{api_port} - reusing it (restart it yourself if you changed builder-api or engine)")
    if web_running:
        say(f"UI already running on :{web_port} - reusing it")
    if api_running and web_running:
        say(f"nothing to start -> http://localhost:{web_port}/builder/")
        return 0

    procs: list[subprocess.Popen] = []
    try:
        if not api_running:
            say(f"API  (headed Chromium)  -> http://127.0.0.1:{api_port}/health")
            procs.append(start([exe("uv"), "run", "builder-api", "--port", str(api_port)], API_DIR))
        if not web_running:
            say(f"UI                     -> http://localhost:{web_port}/builder/")
            env = {**os.environ, "BUILDER_API_URL": f"http://localhost:{api_port}"}
            procs.append(start([exe("npm"), "run", "dev", "--", "--port", str(web_port), "--strictPort"],
                               WEB_DIR, env=env))
        say("Ctrl-C stops both.")

        # Run until either side exits (or Ctrl-C), then take the other one down too.
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        return next(p.returncode for p in procs if p.returncode is not None)
    except KeyboardInterrupt:
        return 130
    finally:
        stop(procs)


if __name__ == "__main__":
    raise SystemExit(main())
