---
id: D-45
title: "Make targets are named silo:action"
date: 2026-11-01
status: implemented
tags: [devops, tooling, standard]
---

## Context and decision

Target names mixed orders (`builder-local`, `test-engine`, `install-builder`,
`setup-env-local`), so finding every command for one app meant guessing. Targets are
now `silo:action`: the silo is the app or area (`engine`, `api`, `builder`, `stack`,
`db`, `demo`, `env`), the action what it does (`make builder:local`, `make engine:test`,
`make stack:up`, `make env:local`). `make help` lists them grouped by section.
Cross-silo aggregates stay bare: `help`, `setup`, `install`, `test`, `lint`, `check`.

| Old | New |
|---|---|
| `setup-env`, `setup-env-local`, `setup-env-force`, `setup-env-local-force` | `env:setup`, `env:local`, `env:force`, `env:local-force` |
| `install-engine`, `install-api`, `install-builder`, `install-browsers` | `engine:install`, `api:install`, `builder:install`, `engine:browsers` |
| `up`, `up-host-ports`, `down`, `nuke`, `restart`, `watch`, `build`, `ps`, `logs` | `stack:up`, `stack:up-host-ports`, `stack:down`, `stack:nuke`, `stack:restart`, `stack:watch`, `stack:build`, `stack:ps`, `stack:logs` |
| `migrate`, `shell-db` | `db:migrate`, `db:shell` |
| `demo`, `graphs`, `demo-site` | `demo:run`, `demo:graphs`, `demo:site` |
| `builder-local`, `builder-api`, `builder-web`, `builder-up`, `builder-down` | `builder:local`, `builder:api`, `builder:web`, `builder:up`, `builder:down` |
| `test-engine`, `test-engine-browser`, `test-api`, `test-api-db`, `test-builder` | `engine:test`, `engine:test-browser`, `api:test`, `api:test-db`, `builder:test` |
| `lint-engine`, `lint-api`, `lint-builder`, `typecheck` | `engine:lint`, `api:lint`, `builder:lint`, `builder:typecheck` |

The old names were removed, not aliased. Older ADRs keep the names they were written
with.

The colon is escaped in rule names (`builder\:local:`). GNU make 3.81, the native
Windows build D-22 supports, does not unescape `\:` in prerequisite lists, so
aggregates run `cd . && "$(MAKE)" --no-print-directory <goals>` instead of listing
prerequisites. `cd . &&` makes make use a shell (sh or cmd.exe), which accepts the
quoted `$(MAKE)` path even when it contains spaces. Recipes stay within D-22's rules.
