---
id: D-15
title: "YAML editor with context-aware completions instead of a form for the whole language"
date: 2026-10-24
status: implemented
tags: [builder, editor, ux]
---

# D-15 · YAML editor with context-aware completions instead of a form for the whole language

*2026-10-24 · implemented*

## Context and decision

A form would re-implement the schema in React and go stale. CodeMirror with Tab-to-accept
completions from three sources chosen by cursor position: the JSON Schema served by
`GET /schema` (keys, enums, verbs, condition kinds — never drifts from the engine), the
graph (`to:` → nodes, `target:`/`visible:` → targets, shapers, `{input}` and
`{requests.<name>.<field>}` templates) and the **live page** (`dom_id:` ids, roles, names,
nearby text, route, alerts). Live lint while typing; Save disabled while it fails.
Generators cover the error-prone bits: **+ target** from an observed element and
**recognize ← page**.
