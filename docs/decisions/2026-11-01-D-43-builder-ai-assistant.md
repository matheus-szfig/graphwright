---
id: D-43
title: Builder AI assistant with private context and reviewed execution
date: 2026-11-01
status: implemented
tags: [builder, ai, security, authoring]
---

## Context and decision

Authors want help adding nodes, targets, requests and code, with or without a running
browser. Builder endpoints expose local debugging data, and `PageState.render` or
event redaction is not an outbound privacy boundary.

- **Builder-only harness.** A collapsible chat panel talks to `builder-api`'s `/ai`
  routes, which call the OpenAI Responses or Anthropic Messages API over fixed URLs
  with plain `httpx` (no SDKs). Keys, capability tokens, conversations and proposals
  live in API memory with expiring connections; keys may also come from
  `BUILDER_AI_*_KEY`. Nothing enters the engine, worker, runs API or database.
- **Privacy projection.** The model sees graph identifiers and structure. Other graph
  literals become opaque path-bound handles restored locally only at their original
  path; defaults are withheld. JavaScript is readable source with private values and
  PII-looking literals as inline handles bound to the same field. Page observations
  expose handles, roles and states only, never page text, URLs, screenshots, input
  values, vars, request results or event payloads. Task text is masked for known
  values and common PII shapes; arbitrary personal data in prose cannot be detected,
  so the panel asks for synthetic examples.
- **Secure inputs.** `type: secure` is the author-facing secret type (`password`
  stays a legacy alias). The harness cannot lower classifications or move opaque
  literals to new paths.
- **Reviewed changes.** Edits accumulate in proposals that are linted and reviewed in
  the panel, and save through `GraphStore` only if the saved base is unchanged.
  Saving and patching a live session are separate; `GraphSession.run(on_step=...)`
  activates a queued patch at the next step boundary without resetting node, vars or
  visits. New inputs a patch needs are typed locally on the proposal; changed input
  declarations, portal or entry URL need a new session.
- **Explicit execution.** Re-detection may run while paused. Browser actions, steps
  and live requests are one-shot proposals bound to a session revision. Arbitrary
  `evaluate` and secret-enabled code cannot run through the AI; code tests use a
  fresh sandbox with synthetic arguments.

The builder image ships `docs/engine-yaml` so the assistant can read the manual.
This is a local authoring aid, not autonomous production repair; the provider still
receives the task text and the approved structural context.
