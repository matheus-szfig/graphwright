---
id: D-37
title: Caller-owned integration and portable action readiness
date: 2026-10-29
status: implemented
tags: [engine, architecture, observer]
---

## Context and decision

The runtime receives inputs and executes the graph. It neither acquires credentials
nor infers what an input identifies. Workers and other callers own provider lookup,
leases, lifecycle, transport, persistence, and event/evidence handling.

Graph requests previously imported global deployment settings and the credential
package through its TOTP helper. Request policy now arrives explicitly through
RequestServices; the outer services factory preserves environment configuration for
worker, builder and CLI callers. TOTP calculation is a pure engine helper with a
compatibility export for providers. Existing callback-based graphs remain supported.
A fresh-process test rejects host-module imports while executing a graph with opaque
inputs, including an input whose credential metadata the runtime must ignore.

Portable actions include capture_image (observer-rendered, secret PNG data URL and
metadata, optional freshness hash), click/fill delay_s, and target-state wait_for.
Clicks wait up to ten seconds for visibility, enabled state, hit testing and stable
geometry (two samples, 100 ms apart, 0.5 px tolerance). timeout_s: 0 checks once.
Ancestor hits no longer make overflow-clipped menu items clickable. Readiness waits
send no pointer input and never retry an already dispatched click. Delays precede
fill template rendering so short-lived values are fetched after the pause.

No model, site, GPU installer, vault protocol or customer flow is part of the engine.
Vision requests use the existing HTTP action with graph-provided inputs. Explicit
non-secret literal fills may opt out of the sensitive-target lint via literal: true.
