---
id: D-16
title: "Friendly lint and one-click fixes"
date: 2026-10-25
status: implemented
tags: [builder]
---

# D-16 · Friendly lint and one-click fixes

*2026-10-25 · implemented*

## Context and decision

Pydantic's "Input should be a valid dictionary or instance of Action" becomes
"actions.3: empty item — give it a verb: …". Credential lints carry a one-click fix that
declares the input and rewrites the fill.
