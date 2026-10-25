---
id: D-07
title: "Pattern syntax: `re:` prefix, `/…/flags`, or substring"
date: 2026-10-24
status: implemented
tags: [engine]
---

# D-07 · Pattern syntax: `re:` prefix, `/…/flags`, or substring

*2026-10-24 · implemented*

## Context and decision

`/regex/` collided with URL paths that start with `/`; `re:` is unambiguous and
case-insensitive by default. The same syntax applies everywhere a graph matches text
(conditions, strategies, `extract` field patterns).
