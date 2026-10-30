---
id: D-38
title: Atomic builder deletion and live session state
date: 2026-10-29
status: implemented
tags: [builder, authoring]
---

## Context and decision

Deletion submits one selection (nodes and incident edges) in one API request. It
removes incoming transitions, jump actions and failure/call references together.
Deleting the start node requires selecting a replacement first. Incomplete deletion
drafts remain inspectable with diagnostics; execution still requires a valid graph.
The canvas updates only from the server response, avoiding optimistic disappearance
when deletion is rejected and competing writes from edge/node callbacks.

Builder WebSockets carry transient session_state frames after engine events have
finished mutating runtime state. The canvas updates its current/running/failed node
and visit counters during Run; these presentation frames are not engine events.
Stop releases the browser but preserves the last summary, page and chronological
event log in UI memory. A successful new run clears them. Reloading the page clears
UI memory. Logs support full JSON export, full-log copy and individual-event copy;
export and copy include the original redacted event payload, not truncated display text.
