---
id: D-25
title: "The runs API is the only intake; it owns the schema; a message carries a run id"
date: 2026-10-25
status: implemented
tags: [architecture, api, queue]
---

# D-25 · The runs API is the only intake; it owns the schema; a message carries a run id

*2026-10-25 · implemented*

## Context and decision

Client apps talk to `api/` (`POST /runs`, status, events, SSE stream, cancel), never to
Redis or the worker. The API validates the graph (exists, lints) and inputs, writes the
`run` row as `queued`, then sends `graphwright.tasks.runs.run_graph(run_id)`. The worker
reads graph, inputs and credential reference from the row. Consequences: nothing a
caller sent travels through Redis; a run is visible before a worker picks it up;
redelivery (`acks_late`) is idempotent because `claim_run` only moves `queued`/`running`
runs; cancellation is a row state the worker checks between steps (plus a revoke).

The API owns Alembic (`api/alembic`) for `run`, `run_event` and `artifact`, and creates the
worker's least-privilege role. Task names and Redis keys live in `graphwright.contract`,
imported by both sides; the API never imports the worker's Celery app.
