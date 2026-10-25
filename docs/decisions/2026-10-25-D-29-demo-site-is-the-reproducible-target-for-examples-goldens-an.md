---
id: D-29
title: "demo-site is the reproducible target for examples, goldens and e2e"
date: 2026-10-25
status: implemented
tags: [testing, demo]
---

# D-29 · demo-site is the reproducible target for examples, goldens and e2e

*2026-10-25 · implemented*

## Context and decision

A white-label repo cannot ship graphs for real third-party sites. `demo-site/` ("Acme
Records", stdlib Python, in-memory) has the shapes the engine must handle — search form,
detail page with fields and a table, not-found outcome, login with a TOTP second step,
and a JSON OTP relay that answers "not yet" a few times before returning a code. Example
graphs in `engine/graphs/` run against it, goldens are captured from it, and the browser
e2e tests start it in-process; compose runs it so `make demo` exercises the whole stack.
