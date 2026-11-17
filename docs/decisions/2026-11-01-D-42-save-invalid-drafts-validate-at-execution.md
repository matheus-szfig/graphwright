---
id: D-42
title: Save invalid drafts, validate at execution; drafts outlive panels
date: 2026-11-01
status: implemented
tags: [builder, engine, authoring, ux]
---

## Context and decision

Rejecting every save that failed graph-wide lint blocked multi-part edits, such as
renaming a target in Graph YAML and then updating its references node by node.
Separately, collapsed sections and inactive tabs unmounted their editors and lost
unsaved text.

**Saving is authoring, not an execution gate.** Graph YAML is stored as submitted
(atomic replace), even when malformed; a malformed node fragment is kept as a string
at its node key so other nodes stay editable. Views carry diagnostics and, when the
schema is unreadable, a safe canvas projection that is never written or executed.
Builder Start, Step, Run and Patch compile the saved source first; Start fails before
a browser exists. The runs API and the worker already reject invalid graphs
(`graph_invalid`); the catalog now reports schema/YAML failures without echoing the
file's text, since drafts may contain typed values. Running sessions are snapshots
until an explicit Patch.

**Drafts live in page memory.** Editable documents and panel state (YAML, code
scratch edits, console history, test inputs, filter drafts, AI chat) sit in an
in-memory workspace keyed by graph, node, action and field; CodeMirror selection,
undo and scroll are restored on remount. Clean drafts follow server updates; dirty
drafts are kept and flagged when their saved counterpart changed. Tabs show WIP
badges. Nothing is written to browser storage; reloading clears it.

The builder also gains a Targets tab (edit, resolve against the session, delete with
references shown) and a collapsed result panel on the canvas.
