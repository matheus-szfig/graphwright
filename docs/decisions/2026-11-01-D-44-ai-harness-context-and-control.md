---
id: D-44
title: AI harness — explorable context, streaming, budgets, interrupt and steer
date: 2026-11-01
status: implemented
tags: [builder, ai, streaming, privacy]
---

## Context and decision

The chat follows the pattern of agentic coding harnesses (Claude Code, Codex): a
compact default view, exploration on demand, visible progress, and a turn the author
can stop or redirect.

- **Explorable context.** The first request carries a selected view, not the whole
  graph, schema and manual. Long strings and large subtrees become `$elided` stubs
  (resource, path, size, line count, hash, first line, outline). Tools:
  `explore_graph`, `list_context`, `search_context` (literal, with path and line) and
  `read_context` (path into structured values, numbered line ranges of text).
  References come from identifier fields and whole-value templates. A small
  pre-selection model is opt-in (`BUILDER_AI_TRIAGE_AUTO`).
- **Text edits.** `propose_edits` supports `replace_text` with a unique snippet; long
  text must have been read or written this turn (sha-checked) before replacement.
- **Cacheable prefix.** System prompt and tools are static; context travels with the
  task. Anthropic requests mark cache breakpoints; usage reports cached input tokens.
- **Streaming.** Both providers stream over SSE. Text passes a privacy buffer that
  withholds unfinished words and possible secret prefixes. Tools run only after a
  complete response. Turns have no default deadline (`BUILDER_AI_TIMEOUT_S=0`);
  10-second heartbeats keep proxies alive. Failures and premature EOF are errors.
- **Output budgets.** Automatic initial budgets (Claude 32k bounded by model metadata,
  OpenAI 4k); explicit output-limit truncation retries at most twice up to
  `BUILDER_AI_OUTPUT_TOKEN_CEILING`, never running tools from a truncated response.
- **Activity and presentation.** The timeline shows rounds, tools (masked arguments
  and results), durations, proposals and outcome; messages render Markdown with raw
  HTML skipped. Proposals are inline review cards with diff and lint.
- **Models.** Connecting needs only a key; the model comes from the provider's live
  catalog and can be switched between turns (history is provider-neutral text).
- **Interrupt and steer.** Esc or the composer's stop button aborts at once;
  `POST /ai/cancel` finishes in the background. Enter during a turn posts
  `POST /ai/steer`; the message reaches the model before its next request. Stopped
  turns enter history with the partial answer, so a follow-up has context.
