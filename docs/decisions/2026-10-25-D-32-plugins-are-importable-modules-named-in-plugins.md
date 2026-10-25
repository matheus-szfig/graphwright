---
id: D-32
title: "Plugins are importable modules named in `PLUGINS`"
date: 2026-10-25
status: implemented
tags: [plugins]
---

# D-32 · Plugins are importable modules named in `PLUGINS`

*2026-10-25 · implemented*

## Context and decision

Shapers, portal aliases (`register_portal_alias`) and credential providers
(`register_provider`) are registered by modules listed in `PLUGINS` and imported at
startup by the worker, the API and the builder, so lint sees exactly what the worker
runs. No entry-point magic: a plugin is any module on the path.
