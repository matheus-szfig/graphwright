---
id: D-13
title: "The repo is the graph store"
date: 2026-10-24
status: implemented
tags: [builder]
---

# D-13 · The repo is the graph store

*2026-10-24 · implemented*

## Context and decision

Every builder mutation goes load → change → lint → write `engine/graphs/<name>.yaml`;
nothing that fails lint is written. Changes are reviewable git diffs. Deleting a graph in
the builder deletes its file (after an in-theme confirm) — git keeps the history.
