---
id: D-09
title: "Shapers are pluggable; the core ships only generic ones"
date: 2026-10-25
status: implemented
tags: [engine, plugins]
---

# D-09 · Shapers are pluggable; the core ships only generic ones

*2026-10-25 · implemented*

## Context and decision

Some sites want an input split or reformatted (a reference number across two fields, a
masked document field). A **shaper** is a named pure function referenced by `fill.shape`;
the compiler validates the name. The core registers generic shapers only (`digits_only`,
`alnum_only`, `upper`, `lower`, `strip`, `collapse_spaces`); site-specific ones are
registered by plugins with `@register_shaper("name")` (D-32), so graphs stay data and the
engine stays white-label.
