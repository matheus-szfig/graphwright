---
id: D-40
title: Global graph resynchronization is opt-in
date: 2026-11-01
status: implemented
tags: [engine, authoring]
---

## Context and decision

A failed recognition used to search every node and move to the first match, even
without a connecting transition. Broad predicates could silently redirect a run and
make the resulting failure hard to interpret.

Workflow `resync` defaults to `disabled`: a recognition failure raises the structural
`RecognitionFailed` error with the expected node, route and predicate reasons, and
states that the node's actions did not run. `on_fail` routing applies as usual.
Transitions, `jump` and the builder's **Go to** are unaffected.

`resync: global` restores declaration-order recovery across all nodes; its `resync`
events stay visible and no match raises `UnknownState`. Transition timeouts now say
that the actions completed but no outgoing transition matched. The worker and the
builder share the policy. Graphs that relied on implicit recovery must opt in or
declare recovery routes; the bundled examples did not need either.
