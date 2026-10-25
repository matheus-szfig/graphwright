---
id: D-08
title: "YAML writer indents block sequences"
date: 2026-10-24
status: implemented
tags: [engine]
---

# D-08 · YAML writer indents block sequences

*2026-10-24 · implemented*

## Context and decision

PyYAML's default leaves lists flush with their parent key; one shared `dump_yaml`
(`_IndentedDumper`) writes `actions:\n  - fill:` everywhere (engine, builder, generators).
