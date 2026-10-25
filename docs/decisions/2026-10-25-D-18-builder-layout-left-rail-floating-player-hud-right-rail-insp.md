---
id: D-18
title: "Builder layout: left rail, floating player HUD, right-rail inspector"
date: 2026-10-25
status: implemented
tags: [builder]
---

# D-18 · Builder layout: left rail, floating player HUD, right-rail inspector

*2026-10-25 · implemented*

## Context and decision

Session controls in a top-center glass toolbar (Start · fast/human · Step F10 · Run F5 ·
Observe · Patch · Go to · Stop · status chip); graph operations and inputs in collapsible
sections on the left; inspector tabs on the right (Node, Graph YAML, Page, Targets,
Requests, Events, Screenshot). Both rails resize and collapse with remembered state.
Confirms and prompts are in-theme dialogs, never native browser dialogs.
