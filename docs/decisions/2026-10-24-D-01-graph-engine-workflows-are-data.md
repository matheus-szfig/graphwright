---
id: D-01
title: "Graph engine: workflows are data"
date: 2026-10-24
status: implemented
tags: [architecture, engine]
---

# D-01 · Graph engine: workflows are data

*2026-10-24 · implemented*

## Context and decision

A workflow is YAML, not code: nodes are recognizable page states, actions are a closed
DSL, predicates are structured and return reasons, targets carry ordered locator
strategies, and business outcomes are terminal nodes rather than errors. Graphs are
compiled once per version and linted; anything that changes a graph — a person, the
builder, a future automated patcher — goes through the same lint
(`engine/src/graphwright/engine/`, `engine/graphs/`).

The step-list runner this replaced (Python closures in a list, no postconditions, raw
coordinate clicks, page-visible globals) was not carried into this repo.
