---
id: D-41
title: Broad page inventory with graph-saved view filters
date: 2026-11-01
status: implemented
tags: [observer, engine, builder, authoring]
---

## Context and decision

Observing only controls and images left out useful recognition, extraction and
capture targets (headings, text, status regions, table cells, containers). Widening
the inventory without display controls would crowd the builder, and filtering the
runtime observation by a UI preference would break existing targets.

The shared observer collects rendered elements across frames and **open** shadow
roots and gives each a category (controls, links, media, status, text, tables,
structure, other). CSS-hidden elements go to a separate `hidden_elements` list with
values omitted; they never satisfy visible predicates or target resolution.
`aria-hidden` becomes a state, not exclusion. `observation.max_elements` (default
2000, at most 20000) bounds collection per frame, separately for visible and hidden
elements; controls, links and media get priority and truncation is counted.

Workflow `page_filter` is presentation only: the builder's Page tab filters by
search, category, role, tag, state, frame, viewport and visibility, defaults to the
visible essentials and renders 100 rows at a time. Saving filters checks the previous
value so concurrent edits are not overwritten.

Generated targets keep their frame and append an observer-generated CSS path as a
last-resort strategy (`>>>` crosses an open shadow root) after name, ID and nearby
strategies. Closed shadow roots, unrendered virtual rows and canvas internals remain
out of reach. More page text is now present in observations, so consumers keep using
the existing redaction boundaries.
