---
id: D-31
title: "Live progress is a Redis key plus pub/sub, relayed as Server-Sent Events"
date: 2026-10-25
status: implemented
tags: [api, queue]
---

# D-31 · Live progress is a Redis key plus pub/sub, relayed as Server-Sent Events

*2026-10-25 · implemented*

## Context and decision

The worker overwrites `graphwright:run:progress:<id>` (latest snapshot, TTL'd) and
publishes the same payload on `graphwright:run:events:<id>`. The API serves the key on
`GET /runs/{id}` and relays the channel on `GET /runs/{id}/stream` (SSE; nginx has
buffering off for `/api/`). Durable history is the `run_event` table (the decision
trail: moves, requests, extracts, failures — not every observation). Celery's own result
backend stays in Redis and is not the source of truth for run status.
