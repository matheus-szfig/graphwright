---
id: D-02
title: "Observation is the shared contract and the only page reader"
date: 2026-10-24
status: implemented
tags: [architecture, observer]
---

# D-02 · Observation is the shared contract and the only page reader

*2026-10-24 · implemented*

## Context and decision

One `PageState` (interactive elements, alerts, headings, table *shapes*, frames, a route
template with parameter names only) feeds predicates, goldens and anything that reasons
about a page. It is produced by `pagestate.js` running in a **CDP isolated world per
frame** — no DOM writes, no main-world globals, no `Runtime.enable`. Warm observations
measured 8–10 ms on real server-rendered sites; a ~2 ms signature drives waits.
Frame-aware ids (`e3`, `f1e3`), `nearby` text as a first-class strategy (label-before
layouts in legacy markup), `dom_id` for stable HTML ids. Waiting = poll the signature
until a transition matches; never `networkidle` or fixed sleeps. Live-DOM reads the
engine needs beyond the PageState (`text_of`, `table_rows` for `extract`) go through the
same isolated world.
