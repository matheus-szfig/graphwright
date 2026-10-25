---
id: D-35
title: "RustFS replaces MinIO as the stack's object store"
date: 2026-10-25
status: implemented
tags: [devops, storage]
---

# D-35 · RustFS replaces MinIO as the stack's object store

*2026-10-25 · implemented*

## Context and decision

Object storage now holds failure evidence (D-34); before that it only backed browser
session jars, which graph runs do not use (`persist_state=False`). Compose runs
**RustFS** (`rustfs/rustfs`, pinned; S3 API on 9000, console on 9001, `/health` for the
healthcheck, credentials via `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`), chosen by the
project owner. The engine and API speak plain S3 through `aiobotocore`
(`S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`), so RustFS, AWS S3 or any
compatible store can be swapped in by configuration alone; nothing in the code knows
which one it is. Verified against RustFS 1.0.4: bucket auto-create, put/get, and
missing-key handling behave as the client expects.
