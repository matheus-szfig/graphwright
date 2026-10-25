---
id: D-03
title: "Failure classes route before any model is consulted"
date: 2026-10-24
status: partial
tags: [architecture, engine]
---

# D-03 · Failure classes route before any model is consulted

*2026-10-24 · partial*

## Context and decision

Transient → retry with backoff; blocked/challenged → gate (future, D-05); desync →
re-sync to whichever node recognizes the page; business outcome → terminal node;
structural → `needs_repair` with the predicate trace; request failures (D-27) → their own
class, not retried by the node. A node may also route any failure to another node with
`on_fail` (D-28). Automated repair tiers (one-action online repair, offline typed graph
patches validated by lint + replay against goldens) are *not built*.
