---
id: D-28
title: "`on_fail` routes a failure to a node as data"
date: 2026-10-25
status: implemented
tags: [engine]
---

# D-28 · `on_fail` routes a failure to a node as data

*2026-10-25 · implemented*

## Context and decision

`on_fail: repair` (default) keeps the old behaviour; `fail` always ends the run as
`failed`; `on_fail: <node>` moves the session there with the redacted failure record in
`vars._error`, so a graph can recover (re-request a code, go back to a form) or end in a
specific outcome. Loop guards (`max_visits`, total steps) still apply.
