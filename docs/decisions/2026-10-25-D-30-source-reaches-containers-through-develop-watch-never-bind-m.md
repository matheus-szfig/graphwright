---
id: D-30
title: "Source reaches containers through `develop.watch`, never bind mounts"
date: 2026-10-25
status: implemented
tags: [devops, docker, standard]
---

# D-30 · Source reaches containers through `develop.watch`, never bind mounts

*2026-10-25 · implemented*

## Context and decision

Every image is the artifact: source and config are `COPY`'d in and `docker compose
watch` syncs (`sync`, `sync+restart`, `rebuild`). Volumes are for persistence only
(postgres, minio). The single exception is `builder-api`'s `engine/graphs` mount — the
builder's job is writing YAML back into the repo. nginx bakes its template into a small
image for the same reason; dev credentials for demo-site come from env vars, not a
mounted file.
