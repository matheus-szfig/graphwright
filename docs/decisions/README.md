# Decisions (ADR log)

One file per decision, `YYYY-MM-DD-D##-slug.md`. Each has frontmatter (`id`, `title`,
`date`, `status`, `tags`) and a *Context and decision* section. **Search this index
first**, then open the file. Statuses: `decided` (agreed, not built), `implemented`,
`partial`, `deferred`, `superseded` (points to the newer entry).

Add an entry for any choice that would surprise someone reading the code: a new file, a
new row here, and if it replaces an older decision mark that one `superseded`. Entries
dated before the repo existed were carried over (white-labelled) from the project
graphwright was extracted from (D-24).

| Date | ID | Decision | Status | Tags |
|---|---|---|---|---|
| 2026-10-24 | D-01 | [Graph engine: workflows are data](2026-10-24-D-01-graph-engine-workflows-are-data.md) | implemented | `architecture` `engine` |
| 2026-10-24 | D-02 | [Observation is the shared contract and the only page reader](2026-10-24-D-02-observation-is-the-shared-contract-and-the-only-page-reader.md) | implemented | `architecture` `observer` |
| 2026-10-24 | D-03 | [Failure classes route before any model is consulted](2026-10-24-D-03-failure-classes-route-before-any-model-is-consulted.md) | partial | `architecture` `engine` |
| 2026-10-24 | D-04 | [Stealth as an identity model: make it real before you spoof it](2026-10-24-D-04-stealth-as-an-identity-model-make-it-real-before-you-spoof-i.md) | decided | `architecture` `stealth` |
| 2026-10-24 | D-05 | [Gates for captcha / bot walls via one solver interface](2026-10-24-D-05-gates-for-captcha-bot-walls-via-one-solver-interface.md) | deferred | `architecture` `captcha` |
| 2026-10-24 | D-06 | [Credentials never live in graphs](2026-10-24-D-06-credentials-never-live-in-graphs.md) | implemented | `architecture` `security` |
| 2026-10-24 | D-07 | [Pattern syntax: `re:` prefix, `/…/flags`, or substring](2026-10-24-D-07-pattern-syntax-re-prefix-flags-or-substring.md) | implemented | `engine` |
| 2026-10-24 | D-08 | [YAML writer indents block sequences](2026-10-24-D-08-yaml-writer-indents-block-sequences.md) | implemented | `engine` |
| 2026-10-25 | D-09 | [Shapers are pluggable; the core ships only generic ones](2026-10-25-D-09-shapers-are-pluggable-the-core-ships-only-generic-ones.md) | implemented | `engine` `plugins` |
| 2026-10-25 | D-10 | [Goldens are PageStates checked into tests, generated from demo-site](2026-10-25-D-10-goldens-are-pagestates-checked-into-tests-generated-from-dem.md) | implemented | `engine` `testing` |
| 2026-10-25 | D-11 | [`extract` reads fields and tables off the page](2026-10-25-D-11-extract-reads-fields-and-tables-off-the-page.md) | implemented | `engine` |
| 2026-10-24 | D-12 | [Builder: a step debugger, not a drawing tool; local only; standalone](2026-10-24-D-12-builder-a-step-debugger-not-a-drawing-tool-local-only-standa.md) | implemented | `builder` |
| 2026-10-24 | D-13 | [The repo is the graph store](2026-10-24-D-13-the-repo-is-the-graph-store.md) | implemented | `builder` |
| 2026-10-24 | D-14 | [React Flow canvas with dagre auto-layout](2026-10-24-D-14-react-flow-canvas-with-dagre-auto-layout.md) | implemented | `builder` |
| 2026-10-24 | D-15 | [YAML editor with context-aware completions instead of a form for the whole language](2026-10-24-D-15-yaml-editor-with-context-aware-completions-instead-of-a-form.md) | implemented | `builder` `editor` `ux` |
| 2026-10-25 | D-16 | [Friendly lint and one-click fixes](2026-10-25-D-16-friendly-lint-and-one-click-fixes.md) | implemented | `builder` |
| 2026-10-25 | D-17 | [The builder editor must survive hot reload](2026-10-25-D-17-the-builder-editor-must-survive-hot-reload.md) | implemented | `builder` |
| 2026-10-25 | D-18 | [Builder layout: left rail, floating player HUD, right-rail inspector](2026-10-25-D-18-builder-layout-left-rail-floating-player-hud-right-rail-insp.md) | implemented | `builder` |
| 2026-10-25 | D-19 | [Builder visual language: the Synthetic Flow design export](2026-10-25-D-19-builder-visual-language-the-synthetic-flow-design-export.md) | implemented | `builder` `design` |
| 2026-10-25 | D-20 | [`make builder:local` is the one command](2026-10-25-D-20-make-builder-local-is-the-one-command.md) | implemented | `builder` `devops` |
| 2026-10-25 | D-21 | [Root `.dockerignore`](2026-10-25-D-21-root-dockerignore.md) | implemented | `devops` `docker` |
| 2026-10-25 | D-22 | [Make targets are thin shells over stdlib Python scripts](2026-10-25-D-22-make-targets-are-thin-shells-over-stdlib-python-scripts.md) | implemented | `devops` `tooling` `standard` |
| 2026-10-25 | D-23 | [Secrets in the worker are objects, not strings; authenticated runs share no cookie jar](2026-10-25-D-23-secrets-in-the-worker-are-objects-not-strings-authenticated.md) | implemented | `security` `engine` |
| 2026-10-25 | D-24 | [A white-label, standalone repo carved out of a product monorepo](2026-10-25-D-24-a-white-label-standalone-repo-carved-out-of-a-product-monore.md) | implemented | `architecture` `repo` |
| 2026-10-25 | D-25 | [The runs API is the only intake; it owns the schema; a message carries a run id](2026-10-25-D-25-the-runs-api-is-the-only-intake-it-owns-the-schema-a-message.md) | implemented | `architecture` `api` `queue` |
| 2026-10-25 | D-26 | [Credentials come from pluggable providers, by reference](2026-10-25-D-26-credentials-come-from-pluggable-providers-by-reference.md) | implemented | `security` `credentials` |
| 2026-10-25 | D-27 | [Graph requests: short-lived values fetched at the moment of use](2026-10-25-D-27-graph-requests-short-lived-values-fetched-at-the-moment-of-u.md) | implemented | `engine` `requests` |
| 2026-10-25 | D-28 | [`on_fail` routes a failure to a node as data](2026-10-25-D-28-on-fail-routes-a-failure-to-a-node-as-data.md) | implemented | `engine` |
| 2026-10-25 | D-29 | [demo-site is the reproducible target for examples, goldens and e2e](2026-10-25-D-29-demo-site-is-the-reproducible-target-for-examples-goldens-an.md) | implemented | `testing` `demo` |
| 2026-10-25 | D-30 | [Source reaches containers through `develop.watch`, never bind mounts](2026-10-25-D-30-source-reaches-containers-through-develop-watch-never-bind-m.md) | implemented | `devops` `docker` `standard` |
| 2026-10-25 | D-31 | [Live progress is a Redis key plus pub/sub, relayed as Server-Sent Events](2026-10-25-D-31-live-progress-is-a-redis-key-plus-pub-sub-relayed-as-server.md) | implemented | `api` `queue` |
| 2026-10-25 | D-32 | [Plugins are importable modules named in `PLUGINS`](2026-10-25-D-32-plugins-are-importable-modules-named-in-plugins.md) | implemented | `plugins` |
| 2026-10-25 | D-33 | [API database tests only against a `*_test` database](2026-10-25-D-33-api-database-tests-only-against-a-test-database.md) | implemented | `api` `testing` |
| 2026-10-25 | D-34 | [Failure evidence is captured from the live page, masked, and stored as artifacts](2026-10-25-D-34-failure-evidence-is-captured-from-the-live-page-masked-and-sto.md) | implemented | `engine` `api` `debugging` `security` |
| 2026-10-25 | D-35 | [RustFS replaces MinIO as the stack's object store](2026-10-25-D-35-rustfs-replaces-minio-as-the-stack-s-object-store.md) | implemented | `devops` `storage` |
| 2026-10-25 | D-36 | [Code nodes are sandboxed JavaScript; `evaluate` runs JavaScript in the page](2026-10-25-D-36-code-nodes-are-sandboxed-javascript-evaluate-runs-js-in-the-pa.md) | implemented | `engine` `security` `code` |
| 2026-10-29 | D-37 | [Caller-owned integration and portable action readiness](2026-10-29-D-37-engine-boundary-and-action-readiness.md) | implemented | `engine` `architecture` `observer` |
| 2026-10-29 | D-38 | [Atomic builder deletion and live session state](2026-10-29-D-38-builder-deletion-and-live-state.md) | implemented | `builder` `authoring` |
| 2026-11-01 | D-39 | [Shared graph JavaScript sources](2026-11-01-D-39-shared-graph-scripts.md) | implemented | `engine` `builder` `code` |
| 2026-11-01 | D-40 | [Global graph resynchronization is opt-in](2026-11-01-D-40-global-resync-is-opt-in.md) | implemented | `engine` `authoring` |
| 2026-11-01 | D-41 | [Broad page inventory with graph-saved view filters](2026-11-01-D-41-page-inventory-and-view-filters.md) | implemented | `observer` `engine` `builder` |
| 2026-11-01 | D-42 | [Save invalid drafts, validate at execution; drafts outlive panels](2026-11-01-D-42-save-invalid-drafts-validate-at-execution.md) | implemented | `builder` `authoring` `ux` |
| 2026-11-01 | D-43 | [Builder AI assistant with private context and reviewed execution](2026-11-01-D-43-builder-ai-assistant.md) | implemented | `builder` `ai` `security` |
| 2026-11-01 | D-44 | [AI harness: explorable context, streaming, budgets, interrupt and steer](2026-11-01-D-44-ai-harness-context-and-control.md) | implemented | `builder` `ai` `privacy` |
| 2026-11-01 | D-45 | [Make targets are named `silo:action`](2026-11-01-D-45-make-targets-are-silo-action.md) | implemented | `devops` `tooling` `standard` |

## Open / deferred

- Automated repair tiers (D-03): online one-action repair, offline typed graph patches
  validated by lint + replay against goldens.
- Identity store, proxies, real Chrome + Xvfb launcher, stealth regression suite (D-04).
- Gates: captcha solvers, bot-wall handling, a human-in-the-loop queue (D-05).
- Subgraphs/macros (`call` nodes and actions are parsed and linted, not executed).
- Per-credential encrypted session jars (D-23); run webhooks/callbacks; multi-tenant API
  keys (runs are visible to every key today); evidence retention/pruning (D-34).
- The observer cannot read closed shadow roots or unrendered virtual rows (D-41).
