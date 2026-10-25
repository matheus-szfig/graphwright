---
id: D-11
title: "`extract` reads fields and tables off the page"
date: 2026-10-25
status: implemented
tags: [engine]
---

# D-11 · `extract` reads fields and tables off the page

*2026-10-25 · implemented*

## Context and decision

`extract: {into, fields: {name: {target|css, pattern, required}}, tables: {name: {match|header, max_rows, required}}}`.
Fields read an element's full text (resolved like any action target, or by css) and can
be narrowed by a pattern with a capture group; tables come back as rows keyed by header
text. A missing required value is a structural failure, so a page redesign stops the run
with evidence instead of returning empty data. (Replaces the earlier stub that recorded
only URL, title and headings.)
