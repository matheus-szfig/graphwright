---
id: D-05
title: "Gates for captcha / bot walls via one solver interface"
date: 2026-10-24
status: deferred
tags: [architecture, captcha]
---

# D-05 · Gates for captcha / bot walls via one solver interface

*2026-10-24 · deferred*

## Context and decision

Ladder: avoid → reuse clearance → solve locally (simple image captchas) → token service →
human-in-the-loop (checkpoint/resume makes parking a run possible) → quarantine.
