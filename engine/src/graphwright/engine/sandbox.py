"""Sandboxed JavaScript for `script` actions (code nodes).

Each call gets a **fresh V8 isolate** (mini-racer) with no I/O at all — no network,
no filesystem, no process, no `require`/`fetch`/`XMLHttpRequest` — a hard memory
limit and a CPU timeout. That is why code nodes are JavaScript and not Python: a
Python snippet would run inside the worker, next to its database credentials and
the run's decrypted secrets, and Python has no in-process sandbox worth the name.

The code is a function body. In scope:

    args     the action's `args`, rendered (templates → values)
    vars     a JSON copy of the session's vars (secrets removed)
    inputs   a JSON copy of the run's non-secret inputs
    console  .log/.info/.warn/.error — captured, returned with the result

It `return`s any JSON-serializable value. Nothing it does reaches the page, the
browser or the worker; the only way out is the return value.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

_MAX_TIMEOUT_MS = 10_000
_MAX_MEMORY_MB = 256
_MAX_LOG_LINES = 200

# The user's body runs inside a strict-mode function; console is replaced by a
# collector; the result crosses the boundary as JSON text only.
_WRAPPER = """
(function () {
  "use strict";
  const __logs = [];
  const __fmt = (a) => { try { return typeof a === "string" ? a : JSON.stringify(a); } catch (_) { return String(a); } };
  const __log = (level) => (...a) => { if (__logs.length < %(max_logs)d) __logs.push(level + " " + a.map(__fmt).join(" ")); };
  const console = { log: __log("log"), info: __log("info"), warn: __log("warn"), error: __log("error"), debug: __log("debug") };
  const args = %(args)s, vars = %(vars)s, inputs = %(inputs)s;
  let __value;
  try {
    __value = (function (args, vars, inputs, console) { %(code)s
    })(args, vars, inputs, console);
  } catch (e) {
    return JSON.stringify({ ok: false, error: (e && e.stack) ? String(e.stack) : String(e), logs: __logs });
  }
  if (__value && typeof __value.then === "function") {
    return JSON.stringify({ ok: false, error: "script returned a Promise — code nodes are synchronous (no I/O to wait for)", logs: __logs });
  }
  let __json;
  try { __json = JSON.stringify(__value === undefined ? null : __value); }
  catch (e) { return JSON.stringify({ ok: false, error: "return value is not JSON-serializable: " + e, logs: __logs }); }
  return JSON.stringify({ ok: true, value: __json, logs: __logs });
})()
"""

_SYNTAX_CHECK = "(function () { try { new Function('args', 'vars', 'inputs', 'console', %s); return ''; } catch (e) { return String(e); } })()"
_ASYNC_SYNTAX_CHECK = (
    "(function () { try { const AF = Object.getPrototypeOf(async function () {}).constructor;"
    " new AF('args', %s); return ''; } catch (e) { return String(e); } })()"
)


@dataclass
class SandboxResult:
    ok: bool
    value: Any = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "value": self.value, "error": self.error, "logs": self.logs, "elapsed_ms": self.elapsed_ms}


def _racer() -> Any:
    from py_mini_racer import MiniRacer

    return MiniRacer()


def run_js(
    code: str,
    *,
    args: Any = None,
    vars: Any = None,
    inputs: Any = None,
    timeout_ms: int = 1000,
    memory_mb: int = 64,
) -> SandboxResult:
    """Run a code-node body in a fresh isolate. Synchronous: call it through
    ``asyncio.to_thread`` — mini-racer refuses a timeout on a thread that runs an
    event loop."""
    from py_mini_racer import JSOOMException, JSTimeoutException

    timeout_ms = max(1, min(int(timeout_ms), _MAX_TIMEOUT_MS))
    memory_mb = max(8, min(int(memory_mb), _MAX_MEMORY_MB))
    try:
        source = _WRAPPER % {
            "args": json.dumps(args if args is not None else {}),
            "vars": json.dumps(vars if vars is not None else {}),
            "inputs": json.dumps(inputs if inputs is not None else {}),
            "code": code,
            "max_logs": _MAX_LOG_LINES,
        }
    except (TypeError, ValueError) as exc:
        return SandboxResult(ok=False, error=f"script arguments are not JSON-serializable: {exc}")

    ctx = _racer()
    t0 = time.perf_counter()
    try:
        ctx.set_hard_memory_limit(memory_mb * 1024 * 1024)
        raw = ctx.eval(source, timeout_sec=timeout_ms / 1000)
    except JSTimeoutException:
        return SandboxResult(ok=False, error=f"script exceeded {timeout_ms} ms", elapsed_ms=_ms(t0))
    except JSOOMException:
        return SandboxResult(ok=False, error=f"script exceeded {memory_mb} MB of memory", elapsed_ms=_ms(t0))
    except Exception as exc:  # noqa: BLE001 — syntax errors and the like
        return SandboxResult(ok=False, error=f"{type(exc).__name__}: {exc}", elapsed_ms=_ms(t0))
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    out = json.loads(raw)
    value = json.loads(out["value"]) if out.get("ok") else None
    return SandboxResult(ok=bool(out.get("ok")), value=value, error=out.get("error"), logs=out.get("logs", []),
                         elapsed_ms=_ms(t0))


@lru_cache(maxsize=512)
def syntax_error(code: str, *, is_async: bool = False) -> str | None:
    """Lint: does this body compile? (`script` bodies are sync, `evaluate` bodies async.)"""
    ctx = _racer()
    try:
        # Compile-only (`new Function` never runs the body), so no timeout is needed —
        # and mini-racer refuses one on a thread with a running event loop (lint runs
        # inside async code: the builder, the API).
        msg = ctx.eval((_ASYNC_SYNTAX_CHECK if is_async else _SYNTAX_CHECK) % json.dumps(code))
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    finally:
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    return msg or None


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
