"""Workflow graph schema.

Pydantic models for the YAML a workflow is written in. Design rules:

- every non-terminal node has `recognize`, so the engine can re-sync without a model;
- actions are a closed DSL — no code in the graph, code lives in registered macros
  and input shapers;
- predicates are structured, not strings, so they compile to closures and an LLM
  patch can be schema-checked;
- business outcomes are terminal nodes, not errors;
- values that exist only for a moment (2FA codes, one-time links) come from
  `requests`, fetched when an action uses them — never at the start of a run.

Pattern strings, three forms:
  `re:<regex>`       case-insensitive regular expression — use this for URL routes,
                     which start with "/" and would be ambiguous in the next form
  `/<regex>/flags`   regular expression with explicit flags (i, m, s)
  anything else      case-insensitive substring match
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- patterns --------------------------------------------------------------------------

_REGEX_LITERAL = re.compile(r"^/(.*)/([ims]*)$", re.DOTALL)


class _IndentedDumper(yaml.SafeDumper):
    """PyYAML's default leaves block sequences flush with their parent key
    (`actions:\\n- fill:`); this indents them (`actions:\\n  - fill:`), which is how
    the graphs are written by hand and what the builder shows."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:  # type: ignore[override]
        return super().increase_indent(flow, False)


def dump_yaml(data: Any) -> str:
    """The one YAML writer for graphs: indented sequences, insertion order, UTF-8."""
    return yaml.dump(data, Dumper=_IndentedDumper, sort_keys=False, allow_unicode=True, width=100)


class Pattern:
    """Compiled form of a pattern string. Not a Pydantic model: it is built by the
    compiler from the plain strings the schema stores."""

    __slots__ = ("source", "_regex")

    def __init__(self, source: str) -> None:
        self.source = source
        m = _REGEX_LITERAL.match(source)
        if source.startswith("re:"):
            self._regex = re.compile(source[3:], re.IGNORECASE)
        elif m:
            flags = 0
            for ch in m.group(2):
                flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}[ch]
            self._regex = re.compile(m.group(1), flags)
        else:
            self._regex = re.compile(re.escape(source), re.IGNORECASE)

    def test(self, text: str | None) -> bool:
        return bool(text) and self._regex.search(text) is not None

    def __repr__(self) -> str:
        return f"Pattern({self.source!r})"


# --- targets -----------------------------------------------------------------------------

FrameSpec = int | str | Literal["any"]
"""Frame selector: index, `/route regex/`, or "any" (default)."""


class Strategy(BaseModel):
    """One way to find a target. Fields are ANDed. `css`/`xpath` need the live DOM
    and are skipped when resolving against a stored PageState."""

    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    name: str | None = None  # accessible name pattern
    dom_id: str | None = None  # pattern against the HTML id attribute (works offline, unlike css)
    nearby: str | None = None  # pattern against any `nearby` string
    landmark: str | None = None  # pattern against `landmark` (e.g. "form#searchForm")
    value: str | None = None
    state: str | None = None  # must be present in `states`
    tag: str | None = None
    in_viewport: bool | None = None
    frame: FrameSpec = "any"
    index: int = 0  # which match to take when several remain (reading order)
    css: str | None = None
    xpath: str | None = None

    @property
    def needs_dom(self) -> bool:
        return self.css is not None or self.xpath is not None

    @model_validator(mode="after")
    def _at_least_one_criterion(self) -> Strategy:
        if not any(
            v is not None
            for v in (self.role, self.name, self.dom_id, self.nearby, self.landmark, self.value,
                      self.state, self.tag, self.in_viewport, self.css, self.xpath)
        ):
            raise ValueError("a strategy needs at least one criterion")
        return self


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    strategies: list[Strategy] = Field(min_length=1)
    frame: FrameSpec = "any"  # default for strategies that leave `frame` as "any"
    unique: bool = False  # if True, resolution fails when a strategy yields >1 match


# --- conditions ---------------------------------------------------------------------------


class RequestCondition(BaseModel):
    """Routes on the last outcome of a graph request: `ok` (ran and every required
    value was extracted), `failed` (ran out of attempts) or `pending` (not run yet)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["ok", "failed", "pending"] = "ok"


class VarCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    op: Literal["eq", "ne", "set", "unset", "match", "gt", "lt"] = "set"
    value: Any = None


class FrameCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int = 1
    max: int | None = None


CONDITION_KEYS: tuple[str, ...] = ("url", "frame_url", "page_type", "visible", "hidden", "text", "var",
                                   "request", "frame_count", "all", "any", "not_", "always")
"""Field names of Condition, one of which must be set. Module-level so tooling (the
builder's completions) can read it — on the class, Pydantic wraps `_KEYS` as a private attr."""

ACTION_VERBS: tuple[str, ...] = ("goto", "click", "fill", "select", "press", "scroll", "wait_for",
                                 "fetch", "extract", "script", "evaluate", "assert_", "jump", "call")


class Condition(BaseModel):
    """Exactly one predicate key (or the boolean combinators)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: str | None = None  # main-frame route or URL
    frame_url: str | None = None  # any frame's route or URL
    page_type: str | None = None
    visible: str | Strategy | None = None  # target name or inline strategy
    hidden: str | Strategy | None = None
    text: str | None = None
    in_: Literal["alerts", "headings", "elements", "title", "any"] = Field(
        default="alerts", alias="in"
    )
    var: VarCondition | None = None
    request: RequestCondition | None = None
    frame_count: FrameCount | None = None
    all: list[Condition] | None = None
    any: list[Condition] | None = None
    not_: Condition | None = Field(default=None, alias="not")
    always: bool | None = None

    _KEYS = CONDITION_KEYS

    @model_validator(mode="after")
    def _exactly_one(self) -> Condition:
        set_keys = [k for k in self._KEYS if getattr(self, k) is not None]
        if len(set_keys) != 1:
            raise ValueError(f"a condition needs exactly one predicate, got {set_keys or 'none'}")
        return self

    @property
    def kind(self) -> str:
        return next(k for k in self._KEYS if getattr(self, k) is not None)


# --- actions ------------------------------------------------------------------------------


class Goto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


class Click(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str


class Fill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    value: str  # template, e.g. "{reference}" or "{requests.otp.code}"
    shape: str | None = None  # registered input shaper (engine/shapers.py)
    clear: bool = True


class Select(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    option: str  # visible option text pattern


class Press(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    target: str | None = None


class Scroll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str | None = None
    to: Literal["top", "bottom"] | None = None
    by: int | None = None


class WaitFor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: Condition
    timeout_s: float = 10.0


class FieldSpec(BaseModel):
    """One extracted value: the text of a target (or a css selector), optionally
    narrowed by a pattern. With a capture group, the first group is the value."""

    model_config = ConfigDict(extra="forbid")

    target: str | None = None
    css: str | None = None
    pattern: str | None = None
    required: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> FieldSpec:
        if (self.target is None) == (self.css is None):
            raise ValueError("a field needs exactly one of `target` or `css`")
        return self


class TableSpec(BaseModel):
    """Rows of a data table, found by its caption/aria-label/id (`match`) or by a
    header it carries (`header`). Each row is an object keyed by header text."""

    model_config = ConfigDict(extra="forbid")

    match: str | None = None
    header: str | None = None
    max_rows: int = 500
    required: bool = False

    @model_validator(mode="after")
    def _one_matcher(self) -> TableSpec:
        if self.match is None and self.header is None:
            raise ValueError("a table needs `match` or `header`")
        return self


class Extract(BaseModel):
    """Read values off the current page into `vars[into]`: `fields` (text of
    elements) and `tables` (rows). A required value that is missing fails the action
    as structural, so a page redesign stops the run with evidence."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    into: str
    schema_name: str | None = Field(default=None, alias="schema")  # free label for consumers
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    tables: dict[str, TableSpec] = Field(default_factory=dict)


class Fetch(BaseModel):
    """Run a graph request now and put its extracted values in `vars[into]`
    (default: the request name). `on_error: continue` records the failure and moves
    on, so a transition can route on `request: {name, status: failed}`."""

    model_config = ConfigDict(extra="forbid")
    request: str
    into: str | None = None
    on_error: Literal["fail", "continue"] = "fail"


class Script(BaseModel):
    """A code node: sandboxed JavaScript (fresh V8 isolate, no I/O, CPU + memory
    limits — engine/sandbox.py). `code` is a function body with `args` (rendered from
    the templates below), `vars` and `inputs` (non-secret copies) and `console` in
    scope; it `return`s a JSON value. With `into` the value goes to `vars[into]`;
    without it the returned object's keys are set as vars directly."""

    model_config = ConfigDict(extra="forbid")
    code: str
    args: dict[str, Any] = Field(default_factory=dict)  # values may be templates: "{reference}", "{vars.x}"
    into: str | None = None
    timeout_ms: int = Field(default=1000, ge=1, le=10_000)
    memory_mb: int = Field(default=64, ge=8, le=256)
    # Secrets (credential inputs, sensitive request values) are refused as args unless
    # this is set; then every string the script returns is treated as a secret too.
    allow_secrets: bool = False


class Evaluate(BaseModel):
    """Arbitrary JavaScript in the page. `script` is an async function body with `args`
    in scope; its (awaited) JSON return value goes to `vars[into]`.

    world: `isolated` (default) runs in the observer's CDP isolated world — it sees and
    can change the DOM, but not the page's own JS globals, and page scripts cannot see
    it. `main` runs in the page's own context (app state, page functions) — powerful,
    and observable by the page: prefer `isolated` unless you need the page's JS."""

    model_config = ConfigDict(extra="forbid")
    script: str
    args: dict[str, Any] = Field(default_factory=dict)
    into: str | None = None
    world: Literal["isolated", "main"] = "isolated"
    frame: int | str = 0  # frame index, or a pattern matched against frame URLs/routes
    timeout_ms: int = Field(default=5000, ge=1, le=60_000)
    allow_secrets: bool = False


class Assert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: Condition
    message: str = ""


class Jump(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str


class Call(BaseModel):
    model_config = ConfigDict(extra="forbid")
    macro: str
    args: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    """One DSL verb per action. YAML: `- {click: {target: search_button}}`."""

    model_config = ConfigDict(extra="forbid")

    goto: Goto | None = None
    click: Click | None = None
    fill: Fill | None = None
    select: Select | None = None
    press: Press | None = None
    scroll: Scroll | None = None
    wait_for: WaitFor | None = None
    fetch: Fetch | None = None
    extract: Extract | None = None
    script: Script | None = None
    evaluate: Evaluate | None = None
    assert_: Assert | None = Field(default=None, alias="assert")
    jump: Jump | None = None
    call: Call | None = None

    _VERBS = ACTION_VERBS

    @model_validator(mode="after")
    def _exactly_one(self) -> Action:
        verbs = [v for v in self._VERBS if getattr(self, v) is not None]
        if len(verbs) != 1:
            raise ValueError(f"an action needs exactly one verb, got {verbs or 'none'}")
        return self

    @property
    def verb(self) -> str:
        return next(v for v in self._VERBS if getattr(self, v) is not None).rstrip("_")

    @property
    def body(self) -> Any:
        return getattr(self, self.verb if self.verb != "assert" else "assert_")

    @property
    def target(self) -> str | None:
        return getattr(self.body, "target", None)


# --- nodes ----------------------------------------------------------------------------------


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str
    when: Condition = Field(default_factory=lambda: Condition(always=True))


class Retry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transient: int = 2
    backoff_s: list[float] = Field(default_factory=lambda: [2.0, 8.0])


TerminalKind = Literal["success", "outcome", "failure"]


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = ""
    recognize: Condition | None = None
    actions: list[Action] = Field(default_factory=list)
    postconditions: list[Condition] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    timeout_s: float = 20.0
    retry: Retry = Field(default_factory=Retry)
    on_fail: str = "repair"  # "repair" | "fail" | <node name>
    max_visits: int = 3
    # subgraph call: run `call` then continue at `then`
    call: str | None = None
    then: str | None = None
    # terminals
    terminal: TerminalKind | None = None
    result: dict[str, Any] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.terminal is not None

    @model_validator(mode="after")
    def _shape(self) -> Node:
        if self.is_terminal:
            if self.actions or self.transitions or self.recognize:
                raise ValueError("terminal nodes carry no recognize/actions/transitions")
            return self
        if self.call:
            if not self.then:
                raise ValueError("a `call` node needs `then`")
            return self
        if self.recognize is None:
            raise ValueError("non-terminal nodes need `recognize`")
        if not self.transitions:
            raise ValueError("non-terminal nodes need at least one transition")
        return self


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "string"  # free label: string, password, email, date, number, url…
    description: str = ""
    required: bool = True
    pii: bool = True  # redact in events/goldens unless explicitly false
    default: Any = None  # used when the run does not provide the input
    # Fill this input from the run's credential (field name: username, password,
    # token, …) instead of from the run's inputs. The value arrives as a Secret.
    credential: str | None = None


# --- requests -------------------------------------------------------------------------------


class Extractor(BaseModel):
    """Where one value comes from in a response. Exactly one source.

    `json`: dotted path with optional indexes, `data.items[0].code` (a leading `$.`
    is accepted). `regex`: searched in the body text — first capture group, or the
    whole match. `header`: a response header (case-insensitive)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    json_path: str | None = Field(default=None, alias="json")
    regex: str | None = None
    header: str | None = None
    required: bool = True

    @model_validator(mode="after")
    def _one_source(self) -> Extractor:
        if sum(v is not None for v in (self.json_path, self.regex, self.header)) != 1:
            raise ValueError("an extractor needs exactly one of json, regex, header")
        return self


class RequestRetry(BaseModel):
    """Attempts include the first one. `on_status` takes codes (429) or classes
    ("5xx"). `until_extracted` keeps polling while a required value is missing —
    the shape of "wait for the 2FA code to arrive"."""

    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(default=1, ge=1, le=50)
    backoff_s: list[float] = Field(default_factory=lambda: [1.0])
    on_status: list[int | str] = Field(default_factory=lambda: ["5xx", 429])
    on_network_error: bool = True
    until_extracted: bool = False


class ImapSpec(BaseModel):
    """Poll a mailbox for a message that arrived after the run started and extract
    from its text (`extract` regexes run over subject + body)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str
    port: int = 993
    username: str  # templates, e.g. "{mail_user}"
    password: str
    folder: str = "INBOX"
    from_: str | None = Field(default=None, alias="from")  # pattern
    subject: str | None = None  # pattern
    max_age_s: float = 600.0  # ignore messages older than this (and older than the run)


class RequestSpec(BaseModel):
    """A value source the graph calls at the moment of use.

    kinds:
      http   any HTTP API — method/url/headers/query/json/body are templates over
             inputs, vars and other requests' values
      totp   an RFC 6238 code: from `secret` (a template, e.g. "{totp_secret}") or,
             when `secret` is empty, from the run's credential provider
      imap   a code sent by e-mail (see ImapSpec)
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: Literal["http", "totp", "imap"] = "http"
    description: str = ""
    # http
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)
    json_body: Any = Field(default=None, alias="json")
    body: str | None = None
    expect_status: list[int | str] = Field(default_factory=lambda: ["2xx"])
    # totp
    secret: str | None = None
    digits: int = 6
    period: int = 30
    # imap
    imap: ImapSpec | None = None
    # all kinds
    timeout_s: float | None = None
    retry: RequestRetry = Field(default_factory=RequestRetry)
    extract: dict[str, Extractor] = Field(default_factory=dict)
    # Extracted values become Secrets (masked in events, typed but never shown).
    sensitive: bool = True
    # none: fetch on every use (codes expire). run: reuse the first good result.
    cache: Literal["none", "run"] = "none"

    @model_validator(mode="after")
    def _kind_fields(self) -> RequestSpec:
        if self.kind == "http" and not self.url:
            raise ValueError("an http request needs `url`")
        if self.kind == "imap" and self.imap is None:
            raise ValueError("an imap request needs `imap`")
        if self.kind == "imap" and not self.extract:
            raise ValueError("an imap request needs `extract` (a regex for the code)")
        if self.kind == "http" and self.json_body is not None and self.body is not None:
            raise ValueError("use `json` or `body`, not both")
        return self

    @property
    def fields(self) -> list[str]:
        """Names a template can read: `{requests.<name>.<field>}`."""
        return ["code"] if self.kind == "totp" and not self.extract else list(self.extract)


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: str
    version: int = 1
    portal: str
    risk: Literal["open", "protected"] = "open"
    entry_url: str | None = None  # where a fresh run navigates before the start node (template)
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    requests: dict[str, RequestSpec] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    start: str
    imports: list[str] = Field(default_factory=list)
    nodes: dict[str, Node]
    targets: dict[str, Target] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Workflow:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def from_yaml_text(cls, text: str) -> Workflow:
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml(self) -> str:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)
        return dump_yaml(data)
