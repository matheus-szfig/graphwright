---
id: D-39
title: Shared graph JavaScript sources
date: 2026-11-01
status: implemented
tags: [engine, builder, authoring, code]
---

## Context and decision

Large inline JavaScript makes node YAML hard to review and impossible to reuse.
Workflows accept `scripts: {name: source}` beside `requests` and `targets`. Sandbox
`script` and observer-owned `evaluate` actions take `ref: name`; an action supplies
exactly one source, its inline field or a reference. Existing graphs are unchanged.

`Workflow.script_source` resolves the reference from the active workflow at
execution time, so a patched session runs the patched body. References are literal
graph-local identifiers, never file paths, URLs or templates. Lint rejects missing
names, invalid identifiers and syntax errors (shared bodies compile as async, so a
sandbox reference to async code fails lint). Moving source never changes privileges:
`args`, `into`, `world`/`frame`, limits and `allow_secrets` stay on the action, and
`evaluate` still goes through the observer.

The YAML writer emits multiline strings as literal blocks so source round-trips
unchanged. The builder offers `ref` completions, resolves references in Code previews
and sandbox tests, and `graphwright script-test` uses the same resolver.
