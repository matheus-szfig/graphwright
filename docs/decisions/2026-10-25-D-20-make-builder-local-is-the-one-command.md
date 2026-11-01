---
id: D-20
title: "`make builder-local` is the one command"
date: 2026-10-25
status: implemented
tags: [builder, devops]
---

# D-20 · `make builder-local` is the one command

*2026-10-25 · implemented*

## Context and decision

Renamed `make builder:local` by D-45 (silo:action target names).

Starts the builder API (headed) + UI, installs deps on first run, reuses whatever is
already listening, Ctrl-C kills both process trees; `NGINX=1` re-points the stack's nginx
at the host so `http://localhost:8080/builder/` works (the nginx config is an envsubst
template for exactly this).
