---
id: D-36
title: "Code nodes are sandboxed JavaScript; `evaluate` runs JavaScript in the page"
date: 2026-10-25
status: implemented
tags: [engine, security, code]
---

# D-36 · Code nodes are sandboxed JavaScript; `evaluate` runs JavaScript in the page

*2026-10-25 · implemented*

## Context and decision

Graphs need a little computation the DSL does not express — reshape an extracted table,
build a date range, derive a value from two inputs — and sometimes need to reach into
the page beyond what the observer exposes. Two different needs, two actions:

**`script` (code node)** — JavaScript in a **fresh V8 isolate per call** (mini-racer,
`engine/sandbox.py`): no network, filesystem, process, `require` or `fetch`; a hard
memory limit (`memory_mb`, ≤256) and a CPU timeout (`timeout_ms`, ≤10 s). The code is a
function body with `args` (rendered templates — a single-template string keeps its type),
`vars` and `inputs` (JSON copies with secrets removed) and a captured `console`; it
returns JSON into `vars[into]`, or — without `into` — sets each returned key as a var.
Secret args are refused unless `allow_secrets: true`, and then every string it returns
becomes a Secret the run's redactor knows. Lint compiles every body.

JavaScript rather than Python: the code runs inside the worker, next to its database
credentials and the run's decrypted secrets. Python has no in-process sandbox that holds
up; V8 isolates are one, with real limits, available as wheels for Python 3.13/3.14 on
Windows and Linux.

**`evaluate`** — arbitrary JavaScript in the page (async body, awaited, JSON result into
`vars[into]`). `world: isolated` (default) runs in the observer's CDP isolated world: it
sees and can change the DOM but not the page's JS globals, and page scripts cannot see
it. `world: main` runs in the page's own context through Playwright — app state, page
functions — at the cost of being observable by the page. `frame` picks a frame by index
or URL pattern. Secret args need `allow_secrets` (they would be handed to the page).

Both emit `script` / `evaluate` events (logs, vars set, timing, masked values); the
builder has a code tester, a code-node kind and an in-session console;
`graphwright script-test GRAPH NODE` runs a node's script from the terminal.

## Consequences

- Code in graphs is data under review like everything else in `graphs/` — keep it short;
  logic that grows belongs in a plugin (shaper) or upstream.
- `evaluate` in `main` world trades stealth for reach; prefer `isolated`.
