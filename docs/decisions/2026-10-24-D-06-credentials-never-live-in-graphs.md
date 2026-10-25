---
id: D-06
title: "Credentials never live in graphs"
date: 2026-10-24
status: implemented
tags: [architecture, security]
---

# D-06 · Credentials never live in graphs

*2026-10-24 · implemented*

## Context and decision

Graph `inputs` (PII by default, redacted in events) are referenced as `{name}`. The
compiler rejects a literal value in a `fill` whose target looks like a user, password,
document, card, OTP or certificate field. Credential values reach a run only through a
credential provider (D-26) and graph inputs that declare `credential: <field>`.
