---
id: D-33
title: "API database tests only against a `*_test` database"
date: 2026-10-25
status: implemented
tags: [api, testing]
---

# D-33 · API database tests only against a `*_test` database

*2026-10-25 · implemented*

## Context and decision

DB fixtures TRUNCATE tables, so they refuse any database whose name doesn't end in
`_test`; `make test-api-db` creates and migrates `graphwright_test` on the compose
postgres, and CI runs them against a service container.
