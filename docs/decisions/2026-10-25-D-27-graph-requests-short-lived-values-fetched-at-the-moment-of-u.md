---
id: D-27
title: "Graph requests: short-lived values fetched at the moment of use"
date: 2026-10-25
status: implemented
tags: [engine, requests]
---

# D-27 · Graph requests: short-lived values fetched at the moment of use

*2026-10-25 · implemented*

## Context and decision

2FA codes, one-time links and tokens expire; fetching them when a run starts is wrong.
Graphs declare `requests` (`kind: http | totp | imap`) with templated method/url/
headers/query/body, `extract` rules (JSON path, regex, header), `sensitive` (values become
Secrets), `cache` (`none` = refetch on every use) and a `retry` policy (attempts, backoff,
`on_status`, network errors, `until_extracted` polling for a code that arrives late).

Use is lazy — `{requests.otp.code}` in any action template runs the request right before
the action — or explicit with `fetch: {request, into, on_error: fail | continue}`.
Transitions branch on outcomes with `request: {name, status: ok | failed | pending}` and on
filled values with `var`. Every call yields a masked per-attempt trace (request line,
status, timing, body excerpt, extracted values), emitted as a `request` event, stored in
`run_event`, shown in the builder's Requests tab and printed by
`graphwright request-test`. Requests may use other requests' values (lint rejects
cycles); `REQUESTS_ALLOWED_HOSTS` bounds where they can go.
