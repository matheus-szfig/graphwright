---
id: D-26
title: "Credentials come from pluggable providers, by reference"
date: 2026-10-25
status: implemented
tags: [security, credentials]
---

# D-26 · Credentials come from pluggable providers, by reference

*2026-10-25 · implemented*

## Context and decision

A run carries `credential_ref`, never values. `CREDENTIAL_PROVIDER` picks how a reference
becomes values: `none` (default; such runs are refused), `file` (YAML/JSON, e.g. a mounted
secret), `env` (`GW_CRED_<REF>_<FIELD>`), or `http` — a lease service
(`POST {url}/leases/{ref}/redeem`, `…/totp`) for deployments with a separate vault, so the
worker never holds a key that decrypts anything. Plugins can register more. A credential
may name its `portal`; a run of a graph for another portal is refused. TOTP codes are
produced when used: locally from a seed (file/env) or by the provider (http).
