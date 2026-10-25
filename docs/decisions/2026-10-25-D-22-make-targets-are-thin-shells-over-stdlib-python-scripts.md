---
id: D-22
title: "Make targets are thin shells over stdlib Python scripts"
date: 2026-10-25
status: implemented
tags: [devops, tooling, standard]
---

# D-22 · Make targets are thin shells over stdlib Python scripts

*2026-10-25 · implemented*

## Context and decision

A Make recipe may only `cd dir && cmd` or run a plain command. Anything with logic,
environment handling or text processing is a **stdlib-only Python script** run through
`uv run --no-project --quiet` (`PY :=` in the Makefile): `scripts/make_help.py`,
`setup_env.py`, `with_env.py`, `test_db.py`, `run_graph.py`, the builder's
`dev_local.py`. One behaviour on Linux, macOS and native Windows `make` (cmd.exe);
scripts carry their own docstring and run without `make`.
