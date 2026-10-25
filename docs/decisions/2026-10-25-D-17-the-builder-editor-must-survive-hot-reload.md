---
id: D-17
title: "The builder editor must survive hot reload"
date: 2026-10-25
status: implemented
tags: [builder]
---

# D-17 · The builder editor must survive hot reload

*2026-10-25 · implemented*

## Context and decision

CodeMirror extensions are built once; HMR of the completions module did not reach them.
The completion source is reached through a ref updated every render; programmatic
document loads don't count as edits; the UI shows an "API outdated" badge and retries
`/schema` when the API predates the UI.
