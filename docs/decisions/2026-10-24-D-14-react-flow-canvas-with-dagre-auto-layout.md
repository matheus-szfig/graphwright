---
id: D-14
title: "React Flow canvas with dagre auto-layout"
date: 2026-10-24
status: implemented
tags: [builder]
---

# D-14 · React Flow canvas with dagre auto-layout

*2026-10-24 · implemented*

## Context and decision

Canvas over a form UI for the graph. Layout is automatic (dagre) and re-run on load;
positions are not persisted, so the YAML stays the only source of truth.
