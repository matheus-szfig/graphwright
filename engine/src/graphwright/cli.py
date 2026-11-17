"""``graphwright`` — the engine's command line.

    graphwright lint [GRAPH ...]                 lint graphs (names or paths; default: all)
    graphwright graphs                           list the catalog with inputs and lint status
    graphwright step GRAPH --input k=v --headed  step a graph in a real browser (interactive)
    graphwright step GRAPH --input k=v --run     … or run it to the end
    graphwright request-test GRAPH REQUEST --input k=v [--var k=v] [--reveal]
                                                 run one graph request and print every attempt
    graphwright script-test GRAPH NODE [--index N] --input k=v [--var k=v]
                                                 run a node's code-node script (sandbox) and print it
    graphwright observe URL [URL ...]            measure the observer on real pages
    graphwright watch RUN_ID                     follow a run's live progress (Redis)

Browsers launch with the worker's machine profile (``BROWSER_DEFAULT_PROFILE``), so
what you see locally is the identity the worker presents.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from graphwright import plugins

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
]


def _out(text: str = "") -> None:
    print(text)


def _kv(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in pairs:
        if "=" not in kv:
            raise SystemExit(f"expected name=value, got {kv!r}")
        k, v = kv.split("=", 1)
        out[k] = v
    return out


def _json_kv(pairs: list[str]) -> dict[str, Any]:
    """name=value where value is JSON when it parses (`n=3`, `x={"a":1}`), else a string."""
    out: dict[str, Any] = {}
    for k, v in _kv(pairs).items():
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def _workflow_path(ref: str) -> Path:
    from graphwright.catalog import graph_path

    p = Path(ref)
    if p.suffix in (".yaml", ".yml") or p.exists():
        return p
    return graph_path(ref)


def _load(ref: str):
    from graphwright.engine import Workflow

    return Workflow.from_yaml(_workflow_path(ref))


async def _browser_context(pw: Any, *, headed: bool):
    from graphwright.browser.profiles import get_profile
    from graphwright.config import settings

    browser = await pw.chromium.launch(headless=not headed, args=_LAUNCH_ARGS)
    context = await browser.new_context(**get_profile(settings.browser_default_profile).context_kwargs())
    return browser, context


# --- lint / graphs ---------------------------------------------------------------------


def cmd_lint(args: argparse.Namespace) -> int:
    from graphwright.catalog import graphs_dir
    from graphwright.engine.compiler import lint

    refs = args.graphs or [str(p) for p in sorted(graphs_dir().glob("*.yaml"))]
    failed = 0
    for ref in refs:
        try:
            errors = lint(_load(ref))
        except Exception as exc:  # noqa: BLE001 — validation errors are the report
            errors = [str(exc)]
        name = Path(ref).stem
        if errors:
            failed += 1
            _out(f"FAIL {name}")
            for e in errors:
                _out(f"     - {e}")
        else:
            _out(f"ok   {name}")
    return 1 if failed else 0


def cmd_graphs(_: argparse.Namespace) -> int:
    from graphwright.catalog import list_graphs

    for g in list_graphs():
        status = "ok" if g.ok else f"{len(g.lint)} lint error(s)"
        _out(f"{g.name}  v{g.version}  portal={g.portal}  [{status}]")
        for name, spec in g.inputs.items():
            flags = [spec.get("type", "string")]
            if spec.get("credential"):
                flags.append(f"credential:{spec['credential']}")
            if not spec.get("required", True) or spec.get("default") is not None:
                flags.append("optional")
            _out(f"    input {name}: {', '.join(flags)}")
        for r in g.requests:
            _out(f"    request {r}")
    return 0


# --- step ------------------------------------------------------------------------------

_QUIET = {"observe"}


def _print_event(ev: Any) -> None:
    if ev.kind in _QUIET:
        return
    data = {k: v for k, v in ev.data.items() if k not in ("trace", "reasons", "attempts", "details")}
    _out(f"  [{ev.kind}] {ev.node or ''} {data if data else ''}")
    for key in ("reasons", "trace", "details"):
        lines = ev.data.get(key)
        if lines:
            for line in lines[:14]:
                _out(f"      {line}")
            if len(lines) > 14:
                _out(f"      … +{len(lines) - 14}")
    for a in ev.data.get("attempts") or []:
        if isinstance(a, dict):
            _out(f"      #{a.get('n')} {a.get('outcome')} {a.get('status') or ''} {a.get('elapsed_ms')}ms {a.get('error') or ''}")


async def _step(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    from graphwright.browser.humanize import StealthBudget
    from graphwright.catalog import InputsInvalid, build_inputs
    from graphwright.credentials import get_provider
    from graphwright.engine import GraphError, Workflow, compile_workflow
    from graphwright.engine.runtime import GraphSession
    from graphwright.engine.secrets import Redactor
    from graphwright.services import request_services

    path = _workflow_path(args.graph)
    try:
        graph = compile_workflow(Workflow.from_yaml(path))
    except GraphError as exc:
        _out(str(exc))
        return 2
    credential = get_provider().redeem(args.credential, run_id="cli") if args.credential else None
    try:
        inputs = build_inputs(graph.workflow, _kv(args.input), credential)
    except InputsInvalid as exc:
        _out(str(exc))
        return 2
    redactor = Redactor(credential.plaintexts if credential else ())
    services = request_services(totp=credential.totp if credential else None, on_secret=redactor.add)

    async with async_playwright() as pw:
        browser, context = await _browser_context(pw, headed=args.headed)
        page = await context.new_page()
        budget = StealthBudget(scale=0.15) if args.fast else None
        session = GraphSession(graph, page, inputs, budget=budget, on_event=_print_event,
                               redactor=redactor, services=services)
        _out(f"graph {graph.workflow.workflow} v{graph.workflow.version} — start at {session.node}")
        await session.start()

        if args.run:
            await session.run()
        else:
            loop = asyncio.get_running_loop()
            while session.status not in ("done", "failed"):
                prompt = f"\n[{session.status}] node={session.node}  (Enter=step, r=run, g <node>, p=patch, s=state, q) > "
                cmd = (await loop.run_in_executor(None, lambda p=prompt: input(p))).strip()
                if cmd == "q":
                    break
                if cmd == "s":
                    _out(session.last_state.render() if session.last_state else "(no observation yet)")
                    continue
                if cmd.startswith("g "):
                    try:
                        session.reset_to(cmd[2:].strip())
                    except KeyError:
                        _out("unknown node")
                    continue
                if cmd == "p":
                    try:
                        session.patch(Workflow.from_yaml(path))
                        _out("patched")
                    except (GraphError, ValueError) as exc:
                        _out(str(exc))
                    continue
                if cmd == "r":
                    await session.run()
                    continue
                await session.step()

        _out(f"\nstatus: {session.status}")
        if session.result:
            _out(f"result: {json.dumps(redactor(session.result), ensure_ascii=False, default=str)}")
        if session.failure:
            _out(f"failure: {session.failure['type']} {session.failure['message']}")
            for line in session.failure["details"][:20]:
                _out(f"    {line}")
        if not args.run and args.headed:
            await asyncio.get_running_loop().run_in_executor(None, lambda: input("\nEnter to close the browser"))
        await browser.close()
    return 0 if session.status == "done" else 1


# --- request-test --------------------------------------------------------------------


async def _request_test(args: argparse.Namespace) -> int:
    from graphwright.credentials import get_provider
    from graphwright.engine import compile_workflow
    from graphwright.services import request_services
    from graphwright.testing import run_request

    graph = compile_workflow(_load(args.graph))
    credential = get_provider().redeem(args.credential, run_id="cli") if args.credential else None
    services = request_services(totp=credential.totp if credential else None)
    report = await run_request(graph.workflow, args.request, _kv(args.input), variables=_json_kv(args.var),
                               credential=credential, services=services, reveal=args.reveal)
    _out(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["ok"] else 1


# --- script-test ---------------------------------------------------------------------


async def _script_test(args: argparse.Namespace) -> int:
    from graphwright.credentials import get_provider
    from graphwright.testing import run_script

    wf = _load(args.graph)
    node = wf.nodes.get(args.node)
    if node is None:
        _out(f"no node {args.node!r}; nodes: {', '.join(wf.nodes)}")
        return 2
    scripts = [a.script for a in node.actions if a.script]
    if not scripts or args.index >= len(scripts):
        _out(f"node {args.node!r} has {len(scripts)} script action(s)")
        return 2
    spec = scripts[args.index]
    credential = get_provider().redeem(args.credential, run_id="cli") if args.credential else None
    report = await run_script(wf, wf.script_source(spec), args=spec.args, inputs=_kv(args.input), variables=_json_kv(args.var),
                              credential=credential, into=spec.into, timeout_ms=spec.timeout_ms, memory_mb=spec.memory_mb,
                              allow_secrets=spec.allow_secrets)
    _out(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["ok"] else 1


# --- observe -------------------------------------------------------------------------


async def _observe(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    from graphwright.observe.observer import Observer

    dump: list[dict[str, Any]] = []
    async with async_playwright() as pw:
        browser, context = await _browser_context(pw, headed=args.headed)
        page = await context.new_page()
        for url in args.urls:
            _out(f"\n=== {url}")
            t = time.perf_counter()
            await page.goto(url, wait_until="domcontentloaded")
            goto_ms = (time.perf_counter() - t) * 1000
            await asyncio.sleep(args.wait)
            obs = Observer(page, max_elements=args.max_elements)
            t = time.perf_counter()
            state = await obs.observe()
            cold_ms = (time.perf_counter() - t) * 1000
            warm: list[float] = []
            for _ in range(args.repeat):
                t = time.perf_counter()
                state = await obs.observe()
                warm.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            await obs.signature()
            sig_ms = (time.perf_counter() - t) * 1000
            s = state.stats
            _out(f"goto (domcontentloaded): {goto_ms:7.1f} ms")
            _out(f"observe cold (worlds+lib): {cold_ms:7.1f} ms")
            if warm:
                _out(f"observe warm x{args.repeat}: p50 {statistics.median(warm):6.1f} ms  max {max(warm):6.1f} ms  "
                     f"(in-page extract {s.extract_ms:.1f} ms)")
            _out(f"signature: {sig_ms:6.1f} ms")
            _out(f"frames: {s.frames}  candidates: {s.candidates}  hidden/skipped: {s.hidden}  kept: {s.kept}  "
                 f"tables: {len(state.tables)}  alerts: {len(state.alerts)}")
            rendered = state.render(max_elements=args.render)
            _out(f"render: {len(rendered)} chars (~{len(rendered) // 4} tokens)")
            if args.render:
                _out("---")
                _out(rendered)
            dump.append(state.model_dump(mode="json"))
            await obs.close()
        await browser.close()
    if args.json:
        args.json.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
        _out(f"\nwrote {args.json}")
    return 0


# --- watch ---------------------------------------------------------------------------


def cmd_watch(args: argparse.Namespace) -> int:
    from graphwright.contract import FINAL_STATUSES
    from graphwright.storage import cache

    if args.list:
        for rid in cache.list_recent_run_ids():
            progress = cache.get_run_progress(rid)
            _out(f"{rid}\t{progress.get('status', '?') if progress else '?'}")
        return 0
    if not args.run_id:
        raise SystemExit("run_id is required unless --list is set")
    last: str | None = None
    while True:
        progress = cache.get_run_progress(args.run_id)
        if progress is None:
            print(f"no progress for run {args.run_id}", file=sys.stderr)
            return 1
        serialized = json.dumps(progress, sort_keys=True)
        if serialized != last:
            _out(serialized)
            last = serialized
        if progress.get("status") in FINAL_STATUSES:
            return 0
        time.sleep(args.interval)


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which cannot print the render's "→" / "…".
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(prog="graphwright", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("lint", help="lint graphs (names or paths; default: every graph in GRAPHS_DIR)")
    p.add_argument("graphs", nargs="*")
    p.set_defaults(fn=cmd_lint)

    p = sub.add_parser("graphs", help="list the graph catalog")
    p.set_defaults(fn=cmd_graphs)

    p = sub.add_parser("step", help="step a graph in a real browser")
    p.add_argument("graph", help="graph name or path to a YAML file")
    p.add_argument("--input", action="append", default=[], help="name=value (repeatable)")
    p.add_argument("--credential", help="credential reference for the configured provider")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--run", action="store_true", help="run to completion without prompting")
    p.add_argument("--fast", action="store_true", help="minimal humanisation pauses")
    p.set_defaults(fn=lambda a: asyncio.run(_step(a)))

    p = sub.add_parser("request-test", help="run one graph request and print its attempts")
    p.add_argument("graph")
    p.add_argument("request")
    p.add_argument("--input", action="append", default=[], help="name=value (repeatable)")
    p.add_argument("--var", action="append", default=[], help="vars.name=value for templates (repeatable)")
    p.add_argument("--credential", help="credential reference for the configured provider")
    p.add_argument("--reveal", action="store_true", help="print extracted secret values (local debugging only)")
    p.set_defaults(fn=lambda a: asyncio.run(_request_test(a)))

    p = sub.add_parser("script-test", help="run a node's code-node script in the sandbox")
    p.add_argument("graph")
    p.add_argument("node")
    p.add_argument("--index", type=int, default=0, help="which script action of the node (default 0)")
    p.add_argument("--input", action="append", default=[], help="name=value (repeatable)")
    p.add_argument("--var", action="append", default=[], help="vars.name=value, value as JSON when it parses (repeatable)")
    p.add_argument("--credential", help="credential reference for the configured provider")
    p.set_defaults(fn=lambda a: asyncio.run(_script_test(a)))

    p = sub.add_parser("observe", help="measure the observer on real pages")
    p.add_argument("urls", nargs="+")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--max-elements", type=int, default=400)
    p.add_argument("--render", type=int, default=60, help="elements to show in the render (0 = skip)")
    p.add_argument("--json", type=Path, help="write all PageStates to this file")
    p.add_argument("--wait", type=float, default=1.0, help="seconds to let the page settle after load")
    p.set_defaults(fn=lambda a: asyncio.run(_observe(a)))

    p = sub.add_parser("watch", help="follow a run's live progress")
    p.add_argument("run_id", nargs="?")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--list", action="store_true")
    p.set_defaults(fn=cmd_watch)

    args = ap.parse_args(argv)
    plugins.load()
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
