# Tutorial: build a recognizable search workflow

[Guide index](README.md) · [Reference](reference.md) · [Complete examples](examples/README.md)

This tutorial builds a search that either extracts a record or reports that no record
exists. The complete file is [record_search.yaml](examples/record_search.yaml). It uses
the fictional Acme Records page contract: `/search`, a textbox named "Reference number",
a "Search" button, and detail pages under `/records/`. See the repository integration
chapter for how to run a target site and open the builder.

## 1. Describe the outcomes before the clicks

List what the website can show:

1. Search form, with a reference textbox.
2. Record detail, with a title and status.
3. A "No record found" alert on the search page.

The graph will have `search_form` and `record_detail` action nodes, `not_found` as a
business outcome, and `done` as success. A missing record is an expected result;
a missing search button is a structural problem.

Choose evidence for each page from the builder's **Page** tab. Prefer a route plus
a distinctive control or heading. Do not copy changing session IDs or full HTML.

## 2. Give the workflow an identity and inputs

```yaml
workflow: record_search
version: 1
portal: acme-records
entry_url: "{base_url}/search"
start: search_form
inputs:
  base_url:
    type: url
    description: Base URL of the test site, without a trailing slash
    pii: false
  reference:
    type: string
    description: Record reference to search for
outputs:
  record: record_v1
```

Save the graph as `record_search.yaml`. `workflow` should agree with the filename
used by the builder/catalog. `portal` identifies the site or credential context;
it does not navigate anywhere. `entry_url` performs the initial navigation.

`{base_url}` and `{reference}` are templates resolved from run inputs. They are not
environment variables or JavaScript expressions. `type` is descriptive metadata;
`type: url` does not by itself validate that the value is a URL. Inputs default to
`required: true` and `pii: true`.

`outputs.record` selects `vars.record` when a terminal runs. `record_v1` is a label,
not a schema definition. Until extraction occurs, that output will be `null`.

## 3. Name the controls

```yaml
targets:
  reference_field:
    description: Reference number textbox
    strategies:
      - role: textbox
        name: 're:^reference number$'
      - role: textbox
        dom_id: 're:^ref$'
  search_button:
    description: Search button
    strategies:
      - role: button
        name: 're:^search$'
```

Within one strategy, criteria are ANDed. Across strategies, the engine tries
alternatives. Here, the field can be identified by accessible name, then by DOM ID.
Both are observable without querying arbitrary HTML from the graph.

`name` is the accessible name reported by the observer, not necessarily the HTML
`name` attribute. `dom_id` is the HTML ID, not the temporary observer ID such as `e12`.
Use **Page → + target** to create a starting definition, then verify it in **Targets**.

`re:` introduces a case-insensitive regular expression. Anchors matter: `Search`
alone also matches "Advanced search". See [patterns](reference.md#patterns).

## 4. Recognize the form and act

```yaml
nodes:
  search_form:
    intent: Search for one record by reference
    recognize:
      all:
        - url: 're:^/search'
        - visible: reference_field
    actions:
      - fill:
          target: reference_field
          value: "{reference}"
          shape: upper
      - click:
          target: search_button
    transitions:
      - to: not_found
        when:
          text: 're:^no record found'
      - to: record_detail
        when:
          url: 're:^/records/'
```

The engine checks recognition before typing. `fill` clears by default, shapes the
rendered input with `upper`, and types it. The click is followed by fresh observations
until a transition matches or the node's transition timeout expires.

Transition order is priority order: **the first matching transition wins**. `text`
searches alerts by default. Use `in: headings` for headings or `in: title` for the
document title. Avoid an unconditional transition while waiting for a new page:
it can win before that page has appeared.

## 5. Extract the result and terminate

Merge these nodes into the existing `nodes` mapping:

```yaml
record_detail:
  recognize:
    all:
      - url: 're:^/records/'
      - text: 're:^record acm-'
        in: headings
  actions:
    - extract:
        into: record
        fields:
          title:
            css: '#record-title'
            required: true
          status:
            css: '#record-status'
  postconditions:
    - var: {name: record.title, op: set}
  transitions:
    - to: done
not_found:
  terminal: outcome
  result: {status: not_found}
done:
  terminal: success
```

Extraction stores an object at `vars.record`. A missing required title stops the
action as a structural failure. An optional missing status becomes `null`.
`var.name` is relative to `vars`, so write `record.title`, not `vars.record.title`.

The transition to `done` has no `when`, so it defaults to `always: true`. That is
appropriate after extraction: there is no new page to await. The engine checks
postconditions after selecting a transition and before moving. A subsequent step
executes the terminal and produces a result similar to:

```json
{
  "terminal": "success",
  "outputs": {
    "record": {"title": "Example permit", "status": "Active"}
  }
}
```

On the not-found path, `terminal` is `outcome`, `status` is `not_found`, and
`outputs.record` is `null` because the extraction never ran.

## 6. Validate and step in the builder

1. Open/create the graph and save the complete YAML in **Graph YAML**. All named
   nodes and targets must exist before the graph will pass lint.
2. Fill `base_url` and a fictional `reference` in **Inputs**.
3. Press **Start**. This opens/navigates the browser and observes; it does not execute
   the first node's actions yet.
4. Press **Step**. Check **Events** for recognition, target resolution, and transition
   reasons. The first step should move from `search_form` to `record_detail` or `not_found`.
5. Step through extraction and the terminal. Inspect `outputs` in the result.
6. Run again with a missing reference to check the outcome branch.
7. Temporarily change a target in a disposable copy to see structural failure evidence.

Saving YAML does not automatically replace the graph in an existing session. **Patch**
loads the saved graph into that session. **Go to selected node** changes the execution
pointer; it does not navigate the browser or clear accumulated variables and counters.
Start a new session for a clean test.

## 7. Add login or 2FA at the right state

Separate login, verification, and the destination page into distinct nodes when their
recognition or failure outcomes differ. Add the OTP fill to the node that recognizes
the verification form:

```yaml
actions:
  - fill:
      target: code_field
      value: "{requests.otp.code}"
  - click:
      target: verify_button
```

Declare `otp` under `requests` using a credential provider, an HTTP service, or email.
The code is fetched at use time. It need not be known when the run starts, and an HTTP
code request need not expose the authenticator seed at all. Continue with
[Requests and 2FA](requests-and-2fa.md).
