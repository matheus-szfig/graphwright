---
id: D-23
title: "Secrets in the worker are objects, not strings; authenticated runs share no cookie jar"
date: 2026-10-25
status: implemented
tags: [security, engine]
---

# D-23 · Secrets in the worker are objects, not strings; authenticated runs share no cookie jar

*2026-10-25 · implemented*

## Context and decision

Credential values and sensitive request values are wrapped in `Secret`
(`engine/secrets.py`): it prints as `***` everywhere Python would print it and opens only
in `ActionContext.render_template`, right before typing. Every run builds a `Redactor`
from the plaintexts it learns (credential values, extracted request values, TOTP codes);
the runtime passes every event and the failure record through it, and the task applies it
to the progress payload and the run's error columns. `fill` reports lengths, never
values, for PII inputs. Celery `result_extended` stays off; messages carry a run id only.

Authenticated runs open the browser context with `persist_state=False` (no per-domain
jar loaded or stored), and refuse to start while `BROWSER_IGNORE_HTTPS_ERRORS` is on. TLS
client certificates go in as Playwright `client_certificates` (bytes, no temp file).

Python cannot guarantee zeroed memory; references are dropped in `finally`, best effort.
Per-credential encrypted session jars are a follow-up; until then each authenticated run
logs in afresh.
