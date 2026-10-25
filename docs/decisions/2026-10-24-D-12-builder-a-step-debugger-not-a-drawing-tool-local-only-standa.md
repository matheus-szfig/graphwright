---
id: D-12
title: "Builder: a step debugger, not a drawing tool; local only; standalone"
date: 2026-10-24
status: implemented
tags: [builder]
---

# D-12 · Builder: a step debugger, not a drawing tool; local only; standalone

*2026-10-24 · implemented*

## Context and decision

The value is stepping a graph against the real site and seeing *why* a transition did or
didn't fire. Headed browser on the developer's machine; **no screencast over the
network**. Standalone apps (`builder-api/`, `builder-web/`) under the `builder` compose
profile, reachable through nginx at `/builder/`; the engine is an editable path
dependency of the builder API, never the reverse; the worker image never installs it.
