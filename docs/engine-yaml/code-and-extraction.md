# Extraction and JavaScript

[Guide index](README.md) · [Reference](reference.md) · [Requests](requests-and-2fa.md)

Use `extract` for structured page data, `script` for computation over data, and
`evaluate` for operations that specifically require the browser's JavaScript context.
The builder's **code** node is an ordinary node initialized with a script action; it is
not a separate `kind` in the workflow schema.

## Shared JavaScript sources

For long JavaScript bodies, use the shared `scripts` map at the workflow root:

```yaml
scripts:
  extract_heading: |
    return {title: document.querySelector(args.selector)?.textContent?.trim() ?? null};
nodes:
  extract_page:
    recognize: {url: 're:/details'}
    actions:
      - evaluate:
          ref: extract_heading
          args: {selector: h1}
          into: page_data
          timeout_ms: 30000
    transitions: [{to: done}]
  done:
    terminal: success
```

This is a fragment; keep the workflow's `workflow`, `portal` and `start` fields.
Use `script: {ref: name, ...}` for sandbox computation instead. Supply exactly one
of the inline source (`script.code` or `evaluate.script`) and `ref`. References
resolve only inside this graph; they do not load files or URLs. Use identifier
names such as `extract_record_page`. Lint checks missing references and syntax.

Keep arguments, output variable and execution options on the action. Moving source
does not change its browser access, secret handling or execution limits. Edit the
shared body in **Graph YAML**; the **Code** tab resolves it for preview/testing and
shows its `scripts.name` origin. Patch a running session after saving source changes.
The YAML writer preserves multiline source as literal blocks.

## Extract fields and tables

```yaml
actions:
  - extract:
      into: record
      schema: record_v1
      fields:
        reference:
          css: h1
          pattern: 're:(ACM-\d{4}-\d{6})'
          required: true
        title:
          css: '#record-title'
          required: true
        status:
          target: status_label
      tables:
        events:
          header: 're:^event$'
          max_rows: 100
          required: true
```

`status_label` must be declared under `targets`. Extraction goes through observer
operations. It refreshes the observation, resolves fields, reads element text, and
reads matching table rows. It stores an object at `vars.record`; `schema`, when supplied,
is included as a label in that object. It does not look up or validate a registered
schema. Prefer explicit `fields`/`tables`; legacy empty extraction behavior is
repository-specific and is described in [integration](integration.md).

Field definitions:

| Field | Requirement / meaning |
|---|---|
| `target` | Named target; choose exactly one of target or css. |
| `css` | Selector; first matched element is used. |
| `pattern` | Optional normal engine pattern; capture group 1 becomes the value, otherwise full match. |
| `required` | `false` by default; missing or empty text causes structural failure when true. |

A field reads element **text**, not arbitrary attributes or an input's JavaScript
value. For other data, use a carefully scoped `evaluate` action. CSS text extraction
is useful for static labels that are not represented as interactive PageState elements.
Optional missing fields appear as `null`. Required failures still leave the partially
extracted object in vars before the action raises an error.

Table definitions:

| Field | Default / meaning |
|---|---|
| `match` | Pattern against table name (caption/label) or its ID when unnamed. |
| `header` | Pattern matching any observed header. |
| `max_rows` | `500`; maximum rows to read. |
| `required` | `false`; fail when no usable matching table was found. |

At least one of `match` or `header` is required. If both are present, matching either
qualifies the table (OR). The first qualifying table is used. Rows are objects keyed
by header text. A present table with zero rows is not necessarily a missing table;
derive and test the row count if emptiness is a business failure. Header changes change
the output keys, so normalize them in a script when downstream consumers need stability.

Declare `outputs: {record: record_v1}` at workflow level to return the object. Extraction
alone stores a variable; it does not automatically publish every extracted value.

## Sandboxed scripts

```yaml
summarize:
  recognize: {url: 're:^/records/'}
  actions:
    - script:
        into: summary
        args:
          rows: "{vars.record.events}"
        code: |
          const rows = args.rows || [];
          return {
            count: rows.length,
            latest: rows.length ? rows[rows.length - 1] : null
          };
  transitions:
    - to: done
```

`code` is a JavaScript **function body**, not a module or function declaration. Return
a JSON-compatible value. Every execution uses a fresh V8 isolate with no DOM, network,
filesystem, process access, `require`, or shared global state. Promises are rejected;
there is no asynchronous I/O to await. Use graph requests for HTTP calls.

Available values:

- `args`: rendered arguments.
- `vars`: a JSON-safe copy of current variables with secrets removed/masked as null
  where necessary; modifying it does not update the live run.
- `inputs`: a JSON-safe copy of inputs without Secret values.
- `console`: captured logging for debugging.

Only the return value updates the run. There is no Python code-node executor.
Syntax is checked by the compiler, but successful syntax checking cannot prove that
the script will terminate, access valid data, or return the right shape.

| Script setting | Default | Bounds |
|---|---|---|
| `timeout_ms` | 1000 | 1–10000 |
| `memory_mb` | 64 | 8–256 |
| `allow_secrets` | false | Explicit permission to pass Secret arguments. |

## Arguments and return values

```yaml
args:
  rows: "{vars.record.events}"
  label: "Record {reference}"
  multiplier: 2
```

An exact template preserves type: `rows` is an array, not its string representation.
An embedded template produces text: `label` is a string. Literal YAML numbers and
booleans remain typed. Nested lists/maps in args are rendered recursively. Request
references in separate args may trigger separate fetches; explicitly fetch once into
vars if several args must share one snapshot.

With `into: summary`, any JSON-compatible returned scalar, array, object, or null goes
to `vars.summary`. `into` must be a single valid identifier, not a path such as
`account.summary`. Avoid names beginning with `_`, which are used by engine state.

Without `into`, return an object whose keys are valid identifiers not beginning with `_`:

```yaml
actions:
  - script:
      code: |
        return {attempt: (vars.attempt || 0) + 1, ready: true};
```

That merges `attempt` and `ready` into top-level vars, replacing existing values with
those keys. Returning a scalar without `into` fails. Script execution errors, timeout,
memory exhaustion, or invalid return shape become structural failures.

### Secrets in code

Prefer keeping credentials and OTPs in request/fill operations. If code genuinely needs
a secret, pass it as an **exact argument template**, e.g. `password: "{account_password}"`,
and explicitly set `allow_secrets: true`. When Secret arguments are detected, returned
strings are wrapped as secrets and registered with the redactor.

Do not concatenate a secret into an argument string or JavaScript source to get around
the check. The Secret marker is preserved for exact templates; interpolated text may
lose that marker. Automatic secrecy propagation is not a guarantee for arbitrary
numeric encodings, derived log text, or values read directly from a page. Return and log
only the minimum information needed, and never embed credentials in saved code.

## JavaScript in the page

```yaml
actions:
  - evaluate:
      world: isolated
      frame: 0
      args:
        selector: '#record-status'
      script: |
        const element = document.querySelector(args.selector);
        return element ? element.textContent.trim() : null;
      into: status_text
      timeout_ms: 5000
```

`script` is an async function body with `args` available; `await` is supported.
The result must be JSON-compatible. It follows the same `into`/object-merge rules as
`script`, except a null/undefined return with no `into` is allowed as a side-effect-only
operation. `vars` and `inputs` are not automatic browser globals: pass the values in args.

| Setting | Meaning |
|---|---|
| `world: isolated` | Default. Separate JS globals from the site's own context; shares the DOM. |
| `world: main` | Site's own JS context, allowing access to its application globals/functions. |
| `frame` | Default `0`; integer index or URL/route pattern matched by the observer. |
| `timeout_ms` | Default 5000; range 1–60000. |
| `allow_secrets` | Default false; required to deliberately pass Secret args into the page. |

An isolated world does not make DOM changes invisible to the site. Both worlds share
the document, and DOM side effects can be observed. `main` additionally exposes execution
to the site's JS context. `evaluate` is not the no-I/O server sandbox used by `script`.

After evaluation, the action context's observation is invalidated because the DOM may
have changed. An evaluation timeout is classified as transient and can be retried by
the node policy; the previous browser-side operation may already have had side effects.
Make side-effecting scripts idempotent or use an appropriate retry policy. Other
evaluation exceptions are generally structural failures.

## Transform, then branch

```yaml
classify:
  recognize: {url: 're:^/records/'}
  actions:
    - script:
        into: decision
        code: |
          const status = String(vars.record.status || '').trim().toLowerCase();
          return {active: status === 'active'};
  transitions:
    - to: done
      when: {var: {name: decision.active, op: eq, value: true}}
    - to: inactive
```

Do not put JavaScript in `when` or `recognize`. Derive a non-sensitive scalar in a
script, then use a structured predicate. The complete
[transform.yaml](examples/transform.yaml) demonstrates input → code → vars → output
without depending on a real website.

## Testing code in the builder

Select a node and open **Code**. Script actions can be tested in the sandbox without
a browser, with inputs, vars, and args. Evaluate actions run in the selected live
session page. The page console supports ad-hoc evaluation and does not save graph code.

Code-tab edits are scratch copies for testing. Save the final body in **Node** or
**Graph YAML**, then **Patch** the live session. Check the reported variables set, not
only the returned value. A standalone script test does not mutate the live session.
