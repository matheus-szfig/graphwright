#!/usr/bin/env python
"""Submit graph runs through the runs API and follow them live (stdlib only).

    uv run --no-project scripts/run_graph.py acme_record_search --input reference=ACM-2024-000123
    uv run --no-project scripts/run_graph.py acme_account_login --credential demo
    uv run --no-project scripts/run_graph.py --list
    uv run --no-project scripts/run_graph.py --demo        # every acme_* example graph

Talks to http://localhost:8080/api by default (``--api`` / ``GW_API_URL``) with the
dev key (``--key`` / ``GW_API_KEY``). Follows ``GET /runs/{id}/stream`` (SSE) until
the run finishes, then prints the stored result or failure. Exit code 0 only if
every run completed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

FINAL = {"completed", "failed", "cancelled"}

# What `--demo` runs: the example graphs and the inputs/credential each needs.
DEMO_RUNS: list[tuple[str, dict[str, str], str | None]] = [
    ("acme_record_search", {"reference": "ACM-2024-000123"}, None),
    ("acme_record_search", {"reference": "ACM-1999-999999"}, None),
    ("acme_account_login", {}, "demo"),
    ("acme_login_relay_otp", {}, "demo"),
]


class Api:
    def __init__(self, base: str, key: str) -> None:
        self.base = base.rstrip("/")
        self.key = key

    def _req(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "X-API-Key": self.key, "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise SystemExit(f"{method} {path} → {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise SystemExit(f"cannot reach {self.base}: {exc.reason} — is the stack up? (make up)") from None

    def get(self, path: str) -> Any:
        return self._req("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self._req("POST", path, body)

    def stream(self, path: str):
        req = urllib.request.Request(self.base + path, headers={"X-API-Key": self.key, "Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=900) as resp:
            data: list[str] = []
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    yield json.loads("\n".join(data))
                    data = []


def follow(api: Api, run_id: str) -> dict[str, Any]:
    last_node = None
    try:
        for snap in api.stream(f"/runs/{run_id}/stream"):
            node, status = snap.get("node"), snap.get("status")
            event = (snap.get("event") or {}).get("kind")
            if node != last_node or event in ("request", "failure", "on_fail", "terminal"):
                print(f"    {status:<9} node={node or '-':<20} {event or ''}")
                last_node = node
            if status in FINAL:
                break
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"    (stream ended: {exc}; falling back to polling)")
    return api.get(f"/runs/{run_id}")


def run_one(api: Api, graph: str, inputs: dict[str, str], credential: str | None) -> bool:
    body: dict[str, Any] = {"graph": graph, "inputs": inputs}
    if credential:
        body["credential_ref"] = credential
    run = api.post("/runs", body)
    print(f"\n▶ {graph} {inputs or ''}{f' credential={credential}' if credential else ''} → run {run['id']}")
    final = follow(api, run["id"])
    status = final.get("status")
    if status == "completed":
        print(f"  ✔ completed — terminal={final.get('terminal')}")
        print("    result: " + json.dumps(final.get("result"), ensure_ascii=False)[:600])
    else:
        print(f"  ✘ {status} — {final.get('error_type')}: {final.get('error_message')}")
    return status == "completed"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("graph", nargs="?")
    ap.add_argument("--input", action="append", default=[], help="name=value (repeatable)")
    ap.add_argument("--credential", help="credential reference (e.g. demo)")
    ap.add_argument("--list", action="store_true", help="list graphs and exit")
    ap.add_argument("--demo", action="store_true", help="run every example graph against demo-site")
    ap.add_argument("--api", default=os.environ.get("GW_API_URL", "http://localhost:8080/api"))
    ap.add_argument("--key", default=os.environ.get("GW_API_KEY", "dev-only-change-me"))
    args = ap.parse_args()
    api = Api(args.api, args.key)

    if args.list:
        for g in api.get("/graphs"):
            status = "ok" if not g.get("lint") else f"{len(g['lint'])} lint error(s)"
            print(f"{g['name']:<28} v{g.get('version')}  [{status}]  inputs: {', '.join(g.get('inputs', {})) or '-'}")
        return 0
    if args.demo:
        results = [run_one(api, g, i, c) for g, i, c in DEMO_RUNS]
        print(f"\n{sum(results)}/{len(results)} runs completed")
        return 0 if all(results) else 1
    if not args.graph:
        ap.error("a graph name is required (or --list / --demo)")
    inputs = dict(kv.split("=", 1) for kv in args.input)
    return 0 if run_one(api, args.graph, inputs, args.credential) else 1


if __name__ == "__main__":
    raise SystemExit(main())
