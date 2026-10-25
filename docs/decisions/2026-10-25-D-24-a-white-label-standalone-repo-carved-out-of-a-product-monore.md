---
id: D-24
title: "A white-label, standalone repo carved out of a product monorepo"
date: 2026-10-25
status: implemented
tags: [architecture, repo]
---

# D-24 · A white-label, standalone repo carved out of a product monorepo

*2026-10-25 · implemented*

## Context and decision

graphwright started as the RPA module and graph builder of a product monorepo. It now
stands alone: no product names, domains, portals or sample data; a fresh git history
(the old history carried product names); the Python package is `graphwright` in a
standard `src/` layout (the old `src`-as-package quirk is gone). The legacy step runner,
empty recorder stubs and unused LLM dependencies were dropped. Monorepo conventions carry
over: one folder per app (`engine/`, `api/`, `builder-api/`, `builder-web/`,
`demo-site/`), each with an `AGENTS.md`; root Makefile over Python scripts; one compose
stack; nginx as the only published port; dated ADRs here.
