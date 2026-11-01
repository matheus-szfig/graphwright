# YAML language reference

[Guide index](README.md) · [Tutorial](tutorial.md) · [Runtime](runtime-and-debugging.md)

This chapter lists the accepted author-facing fields and their defaults. Related
chapters explain [requests](requests-and-2fa.md) and [code/extraction](code-and-extraction.md)
in greater detail. Unless marked required, a field can be omitted. Unknown fields are
rejected by the schema models; arbitrary extra configuration is not forwarded to the browser.

## YAML conventions

- Use spaces for indentation. Maps name objects; lists preserve execution or priority order.
- Quote templates: `value: "{reference}"`. Unquoted braces are YAML collection syntax.
- Prefer single quotes for regexes containing backslashes: `'re:\d+'`. Double-quoted
  YAML interprets escapes and may reject `\d`.
- Use `|` for multiline JavaScript. It preserves newlines; `>` folds them.
- Use `true` and `false` for booleans. Quote string codes such as `"001234"` so leading
  zeroes survive. Quote strings such as `"yes"`, `"no"`, `"on"`, and `"off"` when they
  must remain text: the PyYAML loader has YAML 1.1 boolean behavior.
- Do not duplicate mapping keys. In particular, merge additions into one `nodes`,
  `inputs`, `targets`, and `requests` block.
- Prefer lower-case `snake_case` for workflow, node, target, variable, input, and request
  identifiers. Request names are explicitly linted as `^[a-z][a-z0-9_]*$`; builder APIs
  impose their own filename/node-name checks.

## Workflow document

| Field | Type / default | Meaning |
|---|---|---|
| `workflow` | Required string | Workflow identity; use the catalog filename stem. |
| `version` | Integer, `1` | Revision metadata; no version-based execution dispatch. |
| `portal` | Required string | Site identifier, also used by credential integration. |
| `risk` | `open` or `protected`, `open` | Chooses the normal humanization budget. Does not grant access or solve challenges. |
| `resync` | `disabled` or `global`, `disabled` | Recognition failure stops or follows `on_fail`. Opt into `global` to search every node in declaration order, including nodes without a connecting transition. |
| `entry_url` | Optional string | Templated initial navigation before node execution. |
| `inputs` | Map of input declarations, `{}` | Per-run data and credential field declarations. |
| `requests` | Map of request declarations, `{}` | HTTP/TOTP/IMAP value sources. |
| `scripts` | Map of JavaScript source strings, `{}` | Shared bodies referenced by `script.ref` or `evaluate.ref`. See [code/extraction](code-and-extraction.md). |
| `outputs` | Map of strings, `{}` | Top-level var name to descriptive schema label. |
| `start` | Required string | Initial node name. |
| `nodes` | Required map | Node name to node definition. |
| `targets` | Map, `{}` | Target name to reusable locator definition. |
| `page_filter` | Map, essentials visible | Saved builder Page-tab filters; does not filter recognition or target resolution. See below. |
| `observation.max_elements` | Integer, `2000`, range `1..20000` | Collection budget per frame, separately for visible and hidden elements. Patch a running session after changing it. |
| `imports` | List of strings, `[]` | Reserved metadata; no import/subgraph loader is implemented. |

At least one `terminal: success` must be statically reachable from `start`, even if
most real runs end in a business outcome. Every declared node must be reachable through
transitions, `on_fail` routes, jumps, or declared `then` edges. Static reachability does
not prove a transition condition can actually become true.

## Page inventory and filters

The Page tab starts with visible controls, links, media and status messages. **Show
everything** reveals all collected categories, including hidden elements. Search,
category buttons, frame, viewport, role, tag and state filters keep large pages
manageable. Rows load in groups of 100; the count always reports the complete filtered
result. **Save filters to graph** writes defaults to YAML. Unsaved filter changes stay
in memory across tab switches; **Load graph defaults** discards them.

Previously missing target rows now include:

| Category | Elements and automation uses |
|---|---|
| Controls | ARIA sliders, spin buttons, tree items, menu check/radio items, editable regions; native controls, file inputs and disclosure summaries remain included. |
| Text | All six heading levels, labels, legends, paragraphs, spans and other text containers; recognition and field extraction. |
| Status | Alerts, live regions, progress, meters, output, logs and timers; completion/error/loading detection. |
| Tables | Tables, rows, headers, cells and ARIA grids; target a particular result or extract data. |
| Structure | Forms, dialogs, tabs/panels, navigation, sections, lists, fieldsets, details and frame containers; scoped targets and screenshots. |
| Media | Images, canvases, SVG roots, video, audio, objects and embeds; screenshots and controls. Decorative `aria-hidden` images remain physically targetable and carry an `aria-hidden` state. |
| Other | Plain containers, custom elements, IDs/test hooks, background-image containers, drop zones and elements with delegated event listeners; structural CSS fallback when no accessible name exists. |
| Hidden inventory | CSS-hidden elements, collapsed contents and hidden inputs; inspect and author a target for when it becomes visible. Values are omitted. These rows never satisfy a visible predicate. |

Elements in child frames and **open** shadow roots are observed. New targets retain
their frame and use name, ID and nearby strategies before an observer-generated CSS
path fallback. A CSS fallback may use `>>>` to cross an open shadow root; it requires
the live observer and cannot prove an offline `recognize` predicate. Structural paths
may need repair when the layout changes.

```yaml
page_filter:
  categories: [controls, links, media, status] # [] means every category
  roles: []                                  # exact role names, OR within each list
  tags: []                                   # e.g. [input, textarea]
  states: []                                 # e.g. [disabled, readonly]
  query: ''                                  # literal case-insensitive substring
  frame: null                                # null = every frame
  visibility: visible                        # visible | hidden | all
  viewport: all                              # all | inside | outside
observation:
  max_elements: 2000
```

Fields combine with AND; items in each list combine with OR. Empty lists allow any
value. Query searches IDs, names, values, roles, tags, URLs, nearby text, landmarks,
options and states. Filters affect both Page-tab table and text views, **not** the
shared observation used by automation. The observer reports truncation rather than
silently pretending the inventory is complete; raise `observation.max_elements`,
Patch, then Re-detect if necessary. Controls, links and media have collection priority.

Browser boundaries remain: closed shadow roots, virtualized/unloaded rows and
content that has not been created cannot be enumerated. Canvas drawing primitives,
PDF internals, CSS pseudo-elements and SVG paths without their own role/tabindex are
not separate rows; target their rendered parent for image capture. Script/style and
document metadata nodes are excluded.

## Inputs

```yaml
inputs:
  reference:
    type: string
    description: Reference supplied by the caller
  account_password:
    type: secure
    credential: password
  locale:
    type: string
    required: false
    pii: false
    default: en
```

| Field | Default | Meaning |
|---|---|---|
| `type` | `string` | Free descriptive label; `secure` (legacy alias: `password`) affects tooling/secret handling. No generic type coercion or validation follows from this label. |
| `description` | Empty string | Author-facing explanation. |
| `required` | `true` | Caller/credential integration must supply a value unless a default applies. |
| `pii` | `true` | Marks personal/sensitive input use for masking; not encryption or a permission. |
| `default` | `null` | Fallback value when not supplied. Use only for non-secret constants. |
| `credential` | Absent | Field name in the run's resolved credential; integration-specific. Cannot coexist with a non-null default. |

An optional omitted input is not automatically an empty string. Referencing it in a
template can fail with "template ... is not set". Give it a meaningful default or use
a script to handle its absence. Input validation/provisioning belongs to the runner;
constructing a low-level `GraphSession` directly does not implement all caller checks.
See [integration](integration.md) for the builder and worker differences.

## Templates and value namespaces

| Expression | Source |
|---|---|
| `{reference}` | Declared input `reference`. |
| `{vars.record.title}` | Nested runtime value under `vars`. |
| `{vars.rows[0].Status}` | Indexed list value in a template. |
| `{page.url}` / `{page.title}` | Latest observation's URL/title; these are the only page template fields. |
| `{requests.otp.code}` | Extracted request field; the async action renderer fetches the request before rendering. |

Ordinary templates produce **strings**. `"{count}"` in an HTTP JSON body remains a
string, even if `count` is numeric. A script/evaluate argument that is exactly one
template preserves its underlying type, including arrays, objects, and `Secret` values.
See [typed code arguments](code-and-extraction.md#arguments-and-return-values).

There is no arithmetic, filter syntax, fallback operator, or arbitrary expression
evaluation in templates. `{vars.count + 1}` is not an expression the engine evaluates.
Use a `script` action and return a new variable. Missing recognized template paths fail
at runtime. Compiler checks cover many input/request references, but not every malformed
template or every nested path. A successful lint is not proof a value will exist.

Templates are supported in entry/navigation URLs, fill values, select options, code
arguments, and request URL/header/query/body/JSON/secret/IMAP connection values. They
are **not generally interpolated** in recognition predicates, target definitions,
variable-condition comparison values, terminal `result`, JavaScript bodies, or arbitrary
metadata. Put runtime values in `args` rather than injecting them into JavaScript source.

## Nodes

There is no YAML `kind: code` or `kind: step`. Those are builder creation/display kinds.
A code node is an ordinary node with `script` or `evaluate` actions.

| Field | Default | Meaning |
|---|---|---|
| `intent` | Empty string | Human explanation; not an instruction interpreted by a model. |
| `recognize` | Required for normal nodes | One condition identifying when actions may execute. |
| `actions` | `[]` | Ordered action list; omission makes a pure routing/waiting node. |
| `transitions` | Required nonempty list for normal nodes | Ordered `{to, when}` entries. |
| `postconditions` | `[]` | Conditions all checked after a transition matches. |
| `timeout_s` | `20.0` | Time spent waiting for a matching transition after actions; not an overall node deadline. |
| `retry` | See below | Retry policy for individual actions raising a transient engine error. |
| `on_fail` | `repair` | `repair`, `fail`, or a node name. |
| `max_visits` | `3` | Maximum recognized executions of this node during the session. |
| `terminal` | Absent | `success`, `outcome`, or `failure`. |
| `result` | Absent map | Static terminal result fields. No template rendering. |
| `call` | Absent string | Reserved subgraph name; execution is not implemented. |
| `then` | Required with `call` | Reserved continuation; does not make calls executable. |

Normal nodes need recognition and at least one transition even if they contain a jump.
Terminal nodes cannot contain recognition, actions, or transitions. Keep other execution
settings off terminals: they are completed directly without normal node processing.

Node retry syntax:

```yaml
retry:
  transient: 2
  backoff_s: [2.0, 8.0]
```

This means up to two retries after the first attempt, per action. The last delay is
reused if more retries are configured. Use nonnegative counts/delays and a nonempty
delay list; not all numeric configuration mistakes are caught by the current schema.
This policy is distinct from [request retries](requests-and-2fa.md#retry-and-polling).

Transition syntax:

```yaml
transitions:
  - to: accepted
    when: {var: {name: decision.accepted, op: eq, value: true}}
  - to: rejected
```

`to` is required. Omitted `when` means `{always: true}`. Conditions are evaluated in
order; there is no parallel branch execution or automatic "wait before fallback."

## Patterns

| Form | Interpretation | Example |
|---|---|---|
| Plain string | Case-insensitive substring search | `Search` matches `Advanced search`. |
| `re:...` | Case-insensitive Python regex search | `'re:^search$'` matches only Search, ignoring case. |
| `/.../flags` | Python regex with explicit `i`, `m`, `s` flags | `'/^Search$/'` is case-sensitive; `'/^search$/i'` is not. |

Regexes search rather than full-match unless you anchor them. Prefer `re:` for URL
paths: a string such as `/search/` is recognized as a regex literal, not necessarily the
literal path you intended. URL conditions test both the observed route and full URL.
Routes may normalize changing identifiers and query values, so inspect the actual
**Page** output rather than guessing its representation.

Patterns are used for URL/text predicates, variable `match`, many target fields,
frame selection, extraction field patterns, table matchers, and IMAP sender/subject
filters. **Request extractor `regex` is different:** it is raw Python regex without
the `re:` or `/.../flags` wrapper. Use inline flags such as `(?i)` there.

## Conditions

Each condition contains exactly **one predicate**, optionally its associated fields.
To combine predicates, use `all`, `any`, or `not`:

```yaml
recognize:
  all:
    - url: 're:/verify'
    - visible: code_field
    - not:
        text: 're:session expired'
```

| Predicate | Value | Semantics |
|---|---|---|
| `always` | Boolean | Constant result; `false` is valid. |
| `url` | Pattern | Main page route or URL matches. |
| `frame_url` | Pattern | Any observed frame route or URL matches. |
| `page_type` | String | Exact match against the observer's classification; inspect the observed value. |
| `visible` | Target name or inline strategy | Target resolves against PageState; see visibility caveats below. |
| `hidden` | Target name or inline strategy | Inverse of resolution, including no match. |
| `text` | Pattern | Search strings in `in`, default `alerts`. |
| `var` | `{name, op, value}` | Compare a runtime variable. |
| `request` | `{name, status}` | Test the last request outcome; does not fetch or poll. |
| `frame_count` | `{min: 1, max: null}` | Inclusive bounds on the length of the observed frame list. |
| `all` | List of conditions | All must match. An empty list is true; avoid accidental empty groups. |
| `any` | List of conditions | At least one matches, short-circuiting on success. An empty list is false. |
| `not` | Condition | Negates the nested result. |

`text.in` accepts `alerts`, `headings`, `elements`, `title`, or `any`. `elements`
searches observed element names, values, and nearby strings. `any` combines these
collections; it is not a raw full-page HTML/text search.

`visible` means observed resolution, not guaranteed clickability. The actionability
check may still reject a covered or disabled element. Named targets containing only
CSS/XPath strategies do not resolve in PageState predicates. Do not use DOM-only
strategies as recognition predicates; `hidden` could misleadingly report their absence.
Inline strategies use observed criteria; avoid CSS/XPath there entirely.

### Variable predicates

| Operator | Meaning |
|---|---|
| `set` (default) | Value is neither `null` nor empty string. `0` and `false` count as set. |
| `unset` | Missing, null, or empty string. |
| `eq`, `ne` | Direct equality/inequality with `value`. |
| `match` | Pattern match of the variable's string representation against `value`. |
| `gt`, `lt` | Direct ordered comparison. Keep types compatible; no numeric coercion. |

`name` uses dot-separated paths relative to `vars`. Unlike action templates, variable
condition lookup does not support list-index syntax. Use a script to derive a scalar
before branching. Do not rely on comparisons to decrypt `Secret` values; branch on
request status or explicitly derived, non-sensitive metadata.

Request predicates require `name`; `status` defaults to `ok` and accepts `ok`, `failed`,
or `pending`. Pending means no result has been recorded, not an asynchronous call in flight.

## Targets and strategies

| Target field | Default | Meaning |
|---|---|---|
| `description` | Required string | What the control represents. |
| `strategies` | Required nonempty list | Alternative locator strategies. |
| `frame` | `any` | Default frame selector for strategies without their own. |
| `unique` | `false` | Reject an ambiguous observed strategy and try later strategies. |

| Strategy field | Match / default |
|---|---|
| `role` | Exact observed role, e.g. `textbox`, `button`, `combobox`, `link`. |
| `tag` | Exact observed HTML tag. |
| `name` | Pattern against accessible name. |
| `dom_id` | Pattern against HTML ID. |
| `nearby` | Pattern against any nearby text string. |
| `landmark` | Pattern against observer landmark string. |
| `value` | Pattern against observed value. |
| `state` | Exact member of the observed state list. |
| `in_viewport` | Optional boolean; exact match. |
| `frame` | `any`, integer frame index, or URL/route pattern. |
| `index` | `0`: choose the zero-based match in observed order. Use nonnegative values. |
| `css` | Live DOM selector fallback. |
| `xpath` | Live DOM XPath fallback. |

At least one criterion besides `frame`/`index` is required. Observed criteria within
a strategy are ANDed. Use the actual roles and states shown by the observer.

Resolution has two phases: **all PageState-capable strategies first**, then DOM-only
strategies if none resolved. Consequently a CSS strategy written before an observed
strategy is still a fallback. In the current DOM fallback, additional role/name/state
criteria and `unique` are not applied; frame scoping uses integer indexes rather than
URL-pattern matching. Keep CSS/XPath strategies simple and explicit.

Prefer accessible name → stable DOM ID → nearby/landmark → DOM selector. A broad first
strategy can hide a better later strategy. `unique: true` only helps if the strategy
actually identifies the intended control. A target match is followed by actionability
checks for visibility, viewport, occlusion, and disabled state where relevant.

## Action reference

Each action list item has exactly one verb. The YAML key is `assert`, not the Python
field name `assert_`. Actions execute sequentially; no parallel branches or implicit
transactions are created.

| Verb | Required fields | Optional fields / defaults | Behavior |
|---|---|---|---|
| `goto` | `url` | — | Render URL; navigate until DOM content loaded; invalidate cached observation. |
| `click` | `target` | `timeout_s: 10.0`, `delay_s: 0.0` | Optional delay, then wait for a visible, enabled, uncovered, stable target and humanized click once. `timeout_s: 0` checks once without waiting for stability. |
| `fill` | `target`, `value` | `shape: null`, `clear: true`, `delay_s: 0.0`, `literal: false` | Optional delay before resolving/rendering, then shape, click, clear, type, and verify field contents. Use `wait_for` for field readiness. Use `literal: true` only for deliberate non-secret fixed values. |
| `select` | `target`, `option` | — | Select native option using rendered visible-text pattern. Not a generic custom dropdown driver. |
| `press` | `key` | `target: null` | Optionally click/focus a target, then send the key/chord, e.g. `Enter` or `Control+a`. |
| `scroll` | None | `target`, `to: top/bottom`, `by` | Scroll target into view, else top/bottom, else wheel by signed amount. Specify one mode. |
| `wait_for` | `condition` OR `target` + `state` | `timeout_s: 10.0` | Wait for a condition or a target state (`visible`, `hidden`, `enabled`, `disabled`, `clickable`); timeout is structural failure. Sends no mouse/keyboard input. |
| `fetch` | `request` | `into: null`, `on_error: fail` | Run request; copy fields to `vars[into or request]`; optionally continue on failure. |
| `extract` | `into` | `schema`, `fields: {}`, `tables: {}` | Extract fields/tables into one variable; see extraction chapter. |
| `capture_image` | `target`, `into` | `expected_sha256: null` | Capture rendered pixels as a secret PNG data URL plus dimensions, MIME type and hash; reject a changed hash when requested. See [images](images.md). |
| `script` | Exactly one of `code` or `ref` | `args: {}`, `into`, `timeout_ms: 1000`, `memory_mb: 64`, `allow_secrets: false` | Sandboxed synchronous JavaScript; no browser or I/O. `ref` names a shared graph script. |
| `evaluate` | Exactly one of `script` or `ref` | `args: {}`, `into`, `world: isolated`, `frame: 0`, `timeout_ms: 5000`, `allow_secrets: false` | Async JavaScript function body in a browser frame. `ref` names a shared graph script. |
| `assert` | `condition` | `message: ""` | Check the current available observation and fail structurally if false. Does not wait. |
| `jump` | `to` | — | Move immediately; skip remaining actions, transition selection, and postconditions. |
| `call` | `macro` | `args: {}` | Reserved; raises a not-implemented structural error. |

Example fragments:

```yaml
actions:
  - goto: {url: "{base_url}/search"}
  - wait_for: {condition: {visible: reference_field}, timeout_s: 15}
  - fill: {target: reference_field, value: "{reference}", shape: upper}
  - press: {target: reference_field, key: Enter}
```

Use `wait_for` when later actions within the same node need a changed state. Prefer
transition conditions when changing nodes. `click` automatically waits up to ten
seconds for clickability, including the end of target movement during menu expansion.
This wait re-resolves the target so DOM replacements do not leave a stale element ID.
It polls geometry even when the page signature does not change. A fixed sleep action
does not exist; `delay_s` adds an optional pause specifically before `click` or `fill`.
`assert` can use the cached observation; use a wait or a state-changing action's refresh
when you need a newly observed condition.

```yaml
actions:
  - click:
      target: search_records
      delay_s: 0.3
      timeout_s: 15
  - wait_for:
      target: reference_field
      state: clickable
      timeout_s: 15
  - fill:
      target: reference_field
      value: "{reference}"
      delay_s: 0.2
```

`delay_s` is measured in seconds and happens before the clickability timeout begins.
For fills, it also happens before rendering/fetching lazy values such as OTP codes.
Both timing fields must be finite and nonnegative. `visible` does not imply
uncovered: use `clickable` to check hit testing and stable geometry. `hidden` also
matches a missing target; `enabled`/`disabled` require a resolved element and inspect
its disabled flag. `clickable` may scroll the target into view. The wait itself
never clicks, fills, or submits a form. Permanent obstruction still produces a
structural failure at timeout; node-level transient retries do not bypass it.

Shaper names are registered in the engine, not defined by YAML. Consult the builder's
schema completions and [repository integration](integration.md) for the actual list.

## Terminal results and output projection

```yaml
outputs:
  account: account_v1
nodes:
  done:
    terminal: success
    result: {status: authenticated}
```

This fragment is only the terminal portion of a larger workflow. When it runs, the
engine returns `terminal`, static `result` fields, and `outputs` containing selected
top-level vars. Output mapping values are labels, not nested paths or expressions.
To return `vars.account.profile`, first store the desired projection in its own
top-level variable. Missing selected variables return `null`.

Avoid `terminal` and `outputs` keys inside your custom `result`; they collide with
engine result fields. Do not put `"{vars.account}"` in `result` expecting interpolation.
Terminal `failure` is a declared graph result, distinct from an unhandled execution
failure. See the runtime chapter for how session and worker status differ.

## Constraints that lint does and does not enforce

The compiler checks named transitions, start, failure routes, jumps, referenced targets,
many templates, request names/fields/dependency cycles, credential defaults, reachable
nodes/success, known shapers, and JavaScript syntax. It also rejects suspicious literal
fills into credential/personal-data-like targets. That heuristic is not a complete
secret scanner; never embed secrets in HTTP headers, script bodies, or graph defaults.

Lint cannot establish that a page contains an element, that a regex is semantically
correct, that every runtime var exists, that external authentication will succeed,
that two branches are mutually exclusive, or that an API response belongs to the
correct login challenge. Validate those with controlled tests and real observations.
