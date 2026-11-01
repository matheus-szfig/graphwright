# AI authoring in the builder

Open **AI assistant** at the bottom of the builder. Select OpenAI or Claude, enter
an API key (or leave it empty to use a configured server key) and click **Connect**.
The builder loads the provider's model catalog, picks a default
(`BUILDER_AI_DEFAULT_ANTHROPIC_MODEL` / `BUILDER_AI_DEFAULT_OPENAI_MODEL`, else the
newest non-mini model of the main family) and makes one small provider request to
confirm it can answer. OpenAI entries that cannot drive a chat (embeddings, audio,
image, TTS…) are hidden.

**Switching models mid-conversation.** The model badge in the chat header is a
dropdown: click it, type to filter, and pick another model (arrow keys + Enter work).
The switch applies from the next message; the conversation, proposals and queue are
kept because history is stored as provider-neutral text. Each turn shows the model
that answered it. The badge is locked while a turn runs — stop it or wait. Switching
stays within the connected provider; to change provider, disconnect and connect again. Keys stay in builder API memory
and expire after one idle hour by default; active turns do not expire. Disconnect clears the connection. Server keys
can instead be exported as BUILDER_AI_OPENAI_KEY or BUILDER_AI_ANTHROPIC_KEY. Settings
are declared in `builder_api/config.py` and documented in `builder-api/.env.example`.

Model discovery uses [OpenAI's model catalog](https://developers.openai.com/api/reference/resources/models/methods/list)
and [Claude's model catalog](https://platform.claude.com/docs/en/api/models/list).

## Authoring without a browser

**Enter** sends a message; **Shift+Enter** inserts a new line. During a response,
the composer stays editable: Enter sends your message to the running turn, and the
assistant reads it at its next step (after the current tool or model response),
even if it was about to finish. **Esc**, or the stop button in the composer, stops
the turn immediately; what was asked and the partial answer stay in the
conversation, so you can follow up with "do X instead". A message that
arrives after the turn ended runs as the next turn. Messages a stopped or failed
turn never read wait in the queue; use **Send now** or remove them.

Each turn shows its model rounds, tool calls, durations and outcome. Expand a tool
to inspect its masked arguments and result, including errors. Tool rows appear
before execution finishes; response text streams through local privacy masking.
The header shows the current activity and elapsed
time even when collapsed, and **Stop** remains available. Scrolling up to review
earlier events pauses automatic scrolling; **Latest activity** returns to the end.

Messages render Markdown, including tables, lists, links and fenced code with Copy
controls. Proposals appear inline in the conversation with their diagnostics, diff,
and proposed YAML. Only the latest revision of each proposal has active actions;
saved and discarded reviews remain visible in the history. Quick prompts above the
composer append to the draft without sending it.

Connection errors (including HTTP 422) appear at the top with the API's explanation.
The panel releases its controls after failure and keeps partial work for review.
Provider text streams over HTTP/SSE into a growing Markdown message. A small
privacy buffer holds unfinished words and possible private-value prefixes so a
secret split across tokens cannot leak. Tool arguments execute only after the
provider has completed the response and their JSON is complete.

Generation has no default wall-clock or read timeout, including responses over an
hour. `BUILDER_AI_TIMEOUT_S=0` disables the turn deadline; a positive value sets an
explicit deadline in seconds (for example, `7200`). Remove an older exported
`BUILDER_AI_TIMEOUT_S=120` or set it to `0`. Ten-second SSE heartbeats keep proxies
active; missing updates show a waiting indicator without aborting. **Stop** stays
available. Connection setup, model listing and other short requests still have
finite network timeouts.

An actual provider error, output limit or stream ending before its terminal event
shows a specific failure, preserves partial text, and unlocks the composer, with
**Reuse message** to put the task back into the composer. Network/protocol failures
and graph changes are never retried automatically. Explicit output truncation has
the bounded recovery described below. Restart the builder API after
updating its event protocol; the frontend's hot reload does not restart Python.

### Context selection

The assistant starts with a compact graph summary and relevant resources, rather
than the entire graph, schema and manuals. Selected/current/start nodes and the
items they reference take priority. Long text and large subtrees appear as
`$elided` stubs with their size, line count, hash, first line and an outline
(Markdown headings or JavaScript functions with line numbers); the content is
fetched only when the assistant asks for it:

- `explore_graph(name, depth)`: one item with what it uses, what uses it and its
  neighborhood, or the graph overview when `name` is empty.
- `list_context`: the resource catalog (graph items, schema definitions, manual
  chapters, session, history, large results).
- `search_context(query, resource)`: literal search across every resource, with
  path and line numbers.
- `read_context(resource, path, lines)`: exact content by path into structured
  values, or numbered line ranges of text.

Large tool results are summarized the same way. The full draft remains in API
memory; the assistant must read existing long text before replacing it, and edits
part of it with `replace_text` (an exact, unique snippet) instead of resending it.

**Context selection** in the connect form defaults to local relevance. Choose
**Automatic lightweight model** (Claude Haiku 5.5 or GPT-5 nano), or set
`BUILDER_AI_TRIAGE_AUTO=true`, to let a small model pre-select resources for large
contexts. The selector receives only a masked catalog and task, and returns
resource IDs; it cannot edit or execute anything. Small contexts skip the extra
request. An unavailable model, invalid response or 20-second selector timeout falls
back to local ranking, without blocking the main model.

The system prompt and tool list are identical on every request; the per-turn
context travels with the task. Claude requests mark prompt-cache breakpoints, so
repeated rounds reread that prefix from cache. The turn footer shows cached input
tokens when the provider reports them.

**Context prepared** reports character counts and reduction; these are not exact
token counts. The turn's provider usage includes selector usage. **Preview context**
shows the last prepared context; before a turn it previews local selection.
Defaults are 16,000 initial context characters, 6,000 per tool result page and
96,000 per active model request. Override `BUILDER_AI_CONTEXT_CHARS`,
`BUILDER_AI_TOOL_PAGE_CHARS` and `BUILDER_AI_REQUEST_CHARS` in the API environment.
`BUILDER_AI_TRIAGE_ANTHROPIC_MODEL`, `BUILDER_AI_TRIAGE_OPENAI_MODEL` and
`BUILDER_AI_TRIAGE_TIMEOUT_S` configure the optional selector. Recent conversation
pairs stay within an 8,000-character budget; older history is retrievable. If a
request cannot fit even after moving old tool bodies into resources, the chat
reports a context-budget error and releases its controls.

### Output token budgets

`BUILDER_AI_MAX_OUTPUT_TOKENS=0` selects the automatic initial budget: up to 32,000
for Claude, clamped to the selected model's advertised `max_tokens`; OpenAI keeps
its 4,096-token default. A positive value overrides the initial budget. Remove an
older exported `BUILDER_AI_MAX_OUTPUT_TOKENS=4096` to use the new automatic budget.
Claude metadata is fetched once per connection; verified 5.5/Fable 5.1 IDs have a
128,000-token fallback, while unknown models fall back conservatively to 4,096.

When an explicit output limit truncates a response, the builder can retry that
model request at most twice, increasing its budget up to both the model's limit
and `BUILDER_AI_OUTPUT_TOKEN_CEILING` (default 128,000). The timeline reports the
recovery and labels retained partial text. No tool from a truncated response runs;
earlier completed tools are not replayed. Usage includes discarded attempts.
Set the ceiling lower to bound generation. A larger allowance does not require
the model to use it. Large changes are requested as smaller focused edit batches.

A full context window is a separate condition: clear/reduce the conversation or
task. Increasing the output allowance does not resolve context exhaustion.

Ask for nodes, transitions, targets, code, inputs or API requests. For example:

> Add a GET request to https://otp.example.test/code. The synthetic response is
> {"data":{"otp":"123456"}}. Capture data.otp as code. Use a secure API-token input
> for bearer authentication and fill the existing verification target.

The assistant can read the whole language manual and examples, consult the current
schema, test JSON extraction against a synthetic sample, and test JavaScript without
session data. It prepares a diff and lint diagnostics. Follow-up instructions revise
the pending proposal. **Save graph** applies it only if the saved graph has not changed.
Save/discard manual editor drafts first. Invalid drafts can be saved, but cannot execute.

## During a session

The assistant can re-detect the page, resolve targets, recognize nodes, and propose
targets from observed element handles. Re-detection does not run actions. Live
requests, individual browser actions and steps produce separate review proposals;
review their active graph/action and click **Execute reviewed action**.

**Save & patch session** validates, saves, then activates a graph change. During a
running operation it queues activation for the next step boundary. Current node,
variables and visit counts survive. Completed actions are not replayed. New required
input values are entered in local fields on the proposal and never sent to the model.
Changing existing input declarations, portal or entry URL requires a new session.
A patch that removes the current node is rejected; if the current node changes to an
incompatible node before queued activation, execution pauses with a patch error.

Use the normal builder controls for arbitrary page JavaScript or scripts that opt into
secret access. The AI may author that code, but its execution tools reject those graphs.

## What is sent to the model

**Preview AI context** shows the graph projection and optional page structure.
It toggles a right sidebar inside the chat. Drag its divider, or focus the divider
and use Left/Right, to resize it. Refresh updates the projection; closing it leaves
the conversation and draft untouched.
Secure/PII input values, defaults, runtime variables, request results, screenshots,
page text and raw event payloads are withheld. Existing graph literals are represented
by private handles and restored locally only at their original paths. Never paste
these handles as executable values in a graph yourself.

JavaScript (`scripts`, `script.code`, `evaluate.script`) is sent as source so the
assistant can read and fix it in place. Known private values and PII-looking
literals inside it (emails, national-ID and reference-number shapes, long digit runs, API-key prefixes) are
replaced by inline `__private_N__` handles that restore only inside the same code
field. Keep real personal data and secrets out of code; pass them as inputs.

Graph identifiers and type/structure metadata are visible. Use descriptive identifiers,
not personal names or record numbers. Task text is sent after masking known private
values and common PII patterns; use synthetic samples and never paste real secrets or
personal data in the conversation. The model can reference `{account_password}`
without knowing its value. Use `type: secure` for secrets; `type: password` remains
supported. Keep `pii: true` for personal inputs and `sensitive: true` for OTP requests.

Conversation and draft state survive panel collapse in page memory. Reload clears
frontend state; connection expiry clears backend state. **Stop** cancels AI work;
the normal player Stop closes the browser session. A tool-call/time limit bounds each
turn, and the panel reports provider input/output token usage.

Design decisions: [D-43](../decisions/2026-11-01-D-43-builder-ai-assistant.md) and
[D-44](../decisions/2026-11-01-D-44-ai-harness-context-and-control.md).
