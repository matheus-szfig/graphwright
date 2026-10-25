---
id: D-21
title: "Root `.dockerignore`"
date: 2026-10-25
status: implemented
tags: [devops, docker]
---

# D-21 · Root `.dockerignore`

*2026-10-25 · implemented*

## Context and decision

`api/` and `builder-api/` build from the repo root (path dependency on `engine/`); without
the root ignore file the context includes every venv and node_modules and builds look
hung.
