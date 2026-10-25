---
id: D-04
title: "Stealth as an identity model: make it real before you spoof it"
date: 2026-10-24
status: decided
tags: [architecture, stealth]
---

# D-04 · Stealth as an identity model: make it real before you spoof it

*2026-10-24 · decided*

## Context and decision

Real Chrome (`channel="chrome"`), headed under Xvfb for protected sites; native emulation
for locale/timezone/geo; spoof only GPU strings; no per-call canvas noise (a constant per
identity is what real machines do). An identity binds profile, seeds, proxy session,
per-site jars, health/quarantine; rotate per session, validate before persisting. For
authenticated access the goal becomes "look like this user's usual browser", not
anonymity. *Partially implemented:* machine profiles, `humanize.py` (mouse state in
Python, actionability checks, stealth budget per graph `risk`).
