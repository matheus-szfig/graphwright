---
id: D-34
title: "Failure evidence is captured from the live page, masked, and stored as artifacts"
date: 2026-10-25
status: implemented
tags: [engine, api, debugging, security]
---

# D-34 · Failure evidence is captured from the live page, masked, and stored as artifacts

*2026-10-25 · implemented*

## Context and decision

A failed run's error type and trace say *what* the engine decided, not what the page
looked like — and the page is gone once the browser context closes. So the worker
captures evidence **before** closing it (`graphwright/evidence.py`, called from
`tasks/runs.py::_drive`): a JPEG screenshot (full page), `page.content()`, the last
PageState (JSON + render) and a context record (node, url/route, the failure, the tail of
the decision trail, redacted vars, request statuses). Triggers: the run failed
(`needs_repair`/`failed`), an unexpected error while the page was open, or a
`terminal: failure` outcome; `EVIDENCE_ON=always|never` widens or disables it.

Masking: fields that received a PII or secret value are tracked by the runtime
(`GraphSession.sensitive_elements`) and replaced with bullets on the page before the
screenshot — pixels cannot be redacted afterwards. HTML, PageState and context go through
the run's Redactor; PageState values of those fields become `***`. Each object goes to
`<S3_BUCKET_PREFIX>-evidence` under `runs/<run>/<attempt>/` with an `artifact` row; ids
are attached to `run.failure.evidence` (or `run.result.evidence` for failure terminals).
Capture is best effort with per-step timeouts and never changes the run's outcome.

The API lists (`GET /runs/{id}/artifacts`) and streams (`…/artifacts/{artifact_id}`)
evidence. Captured HTML is served as an attachment with `Content-Security-Policy:
sandbox` so third-party markup never executes in the API's origin.

## Consequences

- Text the site itself renders (e.g. a reference number echoed in a message) is part of
  the evidence by design; only values the run typed from PII inputs/credentials are masked.
- Evidence retention is the bucket's lifecycle policy; nothing prunes it yet.
