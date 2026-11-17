# Execution, failures, and debugging

[Guide index](README.md) · [Reference](reference.md) · [Requests](requests-and-2fa.md)

Understanding the execution loop prevents most surprising graph behavior. YAML order
matters for actions, transition priority, target strategies, and resynchronization.

## What Start, Step, and Run do

**Start** navigates to `entry_url` once, if present, and observes the page. It does not
execute the first node's actions. Without an entry URL, the embedding caller is
responsible for the browser's initial page. The builder starts a fresh context.

**Step** performs the following:

1. Check the global step limit (default 60 at the `GraphSession` level; not a YAML field).
2. If the current node is terminal, finish immediately.
3. Observe and evaluate the current node's `recognize`.
4. If recognition fails, report `RecognitionFailed` before running any actions and
   stop or follow the node's `on_fail` route. Only with workflow `resync: global`,
   search all nodes in declaration order and move to the first match; if none matches,
   report `UnknownState`. A recovery node's actions execute on a subsequent step.
5. Increment the recognized visit count and enforce `max_visits`.
6. Run actions in order, retrying individual transient failures according to node policy.
7. Re-observe and evaluate transitions in order. If none matches, wait for page signature
   changes (with periodic observation) until `timeout_s` expires.
8. Once a transition matches, check all postconditions against that observation.
9. Move to the selected node and pause. A later step executes that node, including terminals.

**Run** repeats steps until a terminal, failure, repair stop, or its step budget.
Reaching a configured iteration limit is not proof of successful completion: inspect status.

## Recognition guards the expected node

By default (`resync: disabled`), a recognition mismatch cannot redirect execution to
an unrelated matching node. Normal routing follows the current node's transitions;
explicit `jump`, `on_fail`, and builder **Go to** remain available. A destination's
recognition is checked on its next step, before its actions run; selecting a transition
does not imply the destination already recognizes the page.

To opt into automatic global recovery, add this at the workflow root:

```yaml
resync: global
```

This permits recovery to any matching node, even without a connecting transition.
The first match in YAML declaration order wins. Existing graphs that relied on automatic
resynchronization must explicitly enable it or declare the desired recovery routes.

Two nodes can recognize the same route but represent different phases. Add distinguishing
controls or runtime vars when possible. For example, a "submit form" node should
recognize the form, while a "summarize extracted data" node can require that extracted
data is set. With global recovery enabled, an unrelated broad node can capture recovery.

`recognize: {always: true}` is useful for pure computation, but it always qualifies
during opt-in global resynchronization too. An always node placed early may mask an unexpected page.
Prefer a route/data predicate, or deliberately place narrow page nodes earlier. This
is especially relevant to the stub created by the builder's **Add code node** action.

## Transition priority and waiting

This fragment does **not** wait for success:

```yaml
transitions:
  - to: done
    when: {visible: success_banner}
  - to: failed
```

On the first observation without the banner, the unconditional second transition wins.
It is a fallback for the current observation, not a timeout handler. For asynchronous
page changes, recognize both success and error states explicitly, and use `on_fail`
for transition timeout handling:

```yaml
transitions:
  - to: done
    when: {visible: success_banner}
  - to: rejected
    when: {text: 're:request rejected'}
timeout_s: 30
on_fail: review
```

This fragment needs its targets and destination nodes. `review` is just a node name;
it does not automatically notify a person. An unconditional fallback is appropriate
after a synchronous computation or completed `fetch`, when the state being classified
is already available.

## Bounded loops

A self-transition re-enters the node and runs its actions again. `max_visits` counts
recognized executions, not time spent waiting for transitions, and not every failed
recognition. On visit `max_visits + 1`, the guard fails before actions run.

```yaml
transitions:
  - to: done
    when: {url: 're:/account'}
  - to: verify
    when: {text: 're:invalid verification code'}
max_visits: 2
on_fail: code_rejected
```

Use this in a `verify` node to permit one further attempt after the first. A fresh
TOTP request can still produce the same code within one time window. Ensure the page's
error marker clears or changes when resubmitting; stale errors can trigger immediate
re-entry. For delivery polling, request retry is usually clearer than a page-node loop.

The global step count includes terminal and resynchronization steps. Builder **Go to**
does not reset counters. Start a new session when testing a clean retry budget.

## Three distinct retry/wait mechanisms

| Mechanism | Applies to | Default |
|---|---|---|
| Node `retry` | The individual action raising `TransientError` | Two retries, delays 2 and 8 seconds. |
| Request `retry` | HTTP/TOTP/IMAP attempt outcomes | One total attempt. |
| Node transition wait | Matching a transition after all actions | 20 seconds. |

`wait_for.timeout_s` is a fourth, explicit per-action condition wait. Increasing a
node's transition timeout does not increase HTTP timeout or request attempts. Retrying
an action can repeat a click, evaluation, or navigation that already had partial side
effects; avoid treating retries as transactional rollback.

## Failure routing

| `on_fail` | Result |
|---|---|
| `repair` (default) | Structural errors stop with `needs_repair`; other engine error classes stop with `failed`. |
| `fail` | Stop with `failed`, including structural errors. |
| A node name | Store a redacted error in `vars._error`, emit routing evidence, and move to that node. |

The special values `repair` and `fail` take precedence over interpreting the value as
a node name. Avoid those names for nodes you want to route to. The global step-limit
guard stops directly rather than following a node's custom recovery route.

Error data contains `class`, `type`, `message`, `details`, optional `code`, `node`, and
the last observed `route`. Branch on stable fields rather than parsing a message:

```yaml
recover:
  recognize: {var: {name: _error, op: set}}
  transitions:
    - to: unavailable
      when: {var: {name: _error.type, op: eq, value: RequestFailed}}
    - to: needs_review
```

`vars._error` remains until something overwrites it or the session ends. It is not a
freshness flag. A recovery node must also recognize correctly; a broad recovery node
can participate in resynchronization like any other node when `resync: global` is enabled.

Common error types:

| Type | Interpretation / first investigation |
|---|---|
| `TargetNotFound` | No observed or live fallback strategy resolved. Inspect Targets and Page. |
| `NotActionable` | Element resolved but is hidden, disabled, off-screen, or covered. |
| `RecognitionFailed` | The expected node did not recognize the page; its actions did not run. Inspect that node's predicate reasons and the incoming transition. Global recovery is disabled. |
| `UnknownState` | Global recovery was enabled, but no node recognized the observation. Inspect page route, alerts, frames, and predicates. |
| `NoTransition` | No transition became true before timeout. Inspect transition traces and unconditional branches. |
| `PostconditionFailed` | A transition matched but an additional required condition did not. |
| `LoopGuard` | Node visits or global steps exceeded the configured bound. |
| `RequestFailed` | Inspect request attempts, status, missing fields, and code. |
| `StructuralError` | General invalid runtime assumption, assertion, script error, or missing template. |
| `TransientError` | Retriable engine operation failed after its attempts. |

Error classes and action exceptions are implementation contracts, not a universal
exception catcher. Arbitrary integration/programming errors may propagate outside
normal graph routing. Do not assume every external failure becomes one particular code.

## Graph results versus execution status

All three declared terminals (`success`, `outcome`, `failure`) finish a low-level
GraphSession with status `done`. The terminal kind is in `result.terminal`. An unhandled
runtime error instead sets `failed` or `needs_repair` and a failure record. Worker/API
wrappers may translate these into product-level statuses differently; consult
[integration](integration.md) before writing consumers.

`terminal: failure` is therefore useful for expected, classified failure outcomes
such as invalid credentials. It does not reproduce the same failure evidence path as
an unexpected structural exception in every wrapper.

## Builder debugging sequence

1. **Graph YAML / Node:** save work at any time, including syntax, schema, and compiler
   errors. Fix errors before execution. A new node is
   attached by the builder so it is reachable, but its default edges are only scaffolding.
   Rewire it deliberately; an earlier always transition can make a later edge useless.
2. **Page:** inspect the actual route, headings, alerts, element names/states, and frames.
   Do not infer the observer's classification from what the page looks like to a human.
3. **Targets:** inspect every attempted strategy and matched observer ID. A broad first
   strategy may resolve the wrong duplicate element without raising an error.
4. **Events:** follow recognize → actions → transitions → move. Read predicate reasons
   and request attempt traces. Use screenshots for context, not as the predicate source.
5. **Requests:** test the exact request with the correct authentication and vars. Session
   testing updates session request outcomes and can affect subsequent branching.
6. **Code:** test transformation inputs and return shape. Page-console commands can
   mutate the real session; they are not automatically written into the graph.
7. Save the corrected YAML, **Patch**, then observe before proceeding. For changed
   credentials, request cache, or loop budgets, use a new session.

## Saving unfinished work

Graph YAML and Node Save buttons (including Ctrl/Cmd+S) accept invalid work. For
example, rename a target in Graph YAML, save the temporary missing-reference error,
then update its node references and save again. Diagnostics remain visible after saving
and reopening. A successful save clears that editor's unsaved WIP badge even if the
saved graph has errors; the separate invalid-graph indicator remains until repaired.

Graph YAML saves retain the submitted text, including malformed YAML. If the whole
document cannot be parsed, repair it in Graph YAML. Invalid nodes in a parseable graph
remain selectable. A node fragment with malformed YAML syntax is stored as a string at
that node key so other nodes stay accessible; opening that node restores the fragment
for repair. This is deliberately invalid engine schema and cannot run. The canvas may
show placeholders for invalid nodes; these placeholders are never written or executed.

Builder Start, Step, Run, and Patch reject an invalid saved graph on the server as well
as in the UI. The runs API rejects it at intake, and worker startup compiles the file before
redeeming a credential or opening the run's browser context; invalid schema/YAML is recorded as `graph_invalid`.
An already executing run retains its previously compiled snapshot. Saving edits does
not mutate that snapshot; after repairing and saving, Patch or start a fresh session.

## Troubleshooting table

| Symptom | Likely cause and next step |
|---|---|
| `unknown node kind 'code'` when adding a node | UI is newer than the running builder API. Restart the actual API process/rebuild container and reload the page. Do not add `kind: code` to YAML. |
| A new node saves but never executes | A preceding unconditional edge shadows it, or the graph never reaches its source. Read transition order. |
| A node immediately jumps to a different node | Inspect `resync`, `jump`, `on_fail`, and `reset` events. Global resynchronization requires `resync: global`. |
| `text` never matches a heading | Default scope is alerts; add `in: headings`. |
| CSS action works but recognition does not | Named CSS-only targets cannot be resolved from PageState predicates. |
| OTP service is called twice | Both explicit fetch and a lazy request template were used, or multiple args rendered independently. Reuse the fetched var. |
| Two TOTP request names return the same code | Both use the same run-level provider callback. Select explicit secrets or distinct remote credential IDs. |
| HTTP 401 / 403 | The engine does not inherit browser login. Check token validity, scope, ownership, and required password confirmation. |
| Request test works but node fails | Test inputs/vars/credential/cache differ, code expired, or target/actionability failed. |
| `template ... is not set` | Missing input, absent optional extraction, wrong nesting, or a variable producer did not run. |
| `var` comparison fails unexpectedly | Condition paths omit `vars.`; request values are strings; list indexing is not supported in var predicates. |
| Script cannot access a password in `inputs` | Secret values are excluded. Use an explicit argument only when necessary and permitted. |
| Code edits disappear | Code-tab edits are scratch; save the YAML. |
| Save succeeded but live behavior is old | Patch the running session; restart API for server code changes. |
| Empty/missing output | `outputs` selects top-level vars by key; labels do not build or validate objects. |

## Validation levels

1. **Parse/schema:** correct YAML types and accepted fields.
2. **Compile/lint:** graph references, reachability, request dependencies, script syntax.
3. **Pure evaluation:** predicates against stored PageStates and scripts against sample data.
4. **Mock services:** retry, extraction, and failure routing with deterministic responses.
5. **Live step/run:** actual pages, browser timing, authorization, and code expiry.

The documentation example checker covers schema/lint, YAML snippets, and local links.
It does not contact external services or claim end-to-end verification. For real
automation, cover success, expected business outcomes, unavailable code, expired auth,
changed page structure, and retry exhaustion before unattended execution.
