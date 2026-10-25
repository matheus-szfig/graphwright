"""Engine core tests against goldens captured from the demo site and the legacy-portal
fixture (tests/capture_goldens.py). Pure Python — compile, recognize, resolve
targets, evaluate transitions, lint."""

from __future__ import annotations

import json

import pytest
from conftest import GOLDENS, GRAPHS

from graphwright import plugins
from graphwright.catalog import load_graph
from graphwright.engine import GraphError, Workflow, compile_workflow, shapers
from graphwright.engine.compiler import lint, targets_report
from graphwright.engine.schema import Action, Condition, Node, Pattern, Strategy
from graphwright.engine.targets import match_strategy
from graphwright.observe.pagestate import PageState

EXAMPLE_GRAPHS = sorted(p.stem for p in GRAPHS.glob("*.yaml"))


def golden(name: str) -> PageState:
    return PageState.model_validate(json.loads((GOLDENS / f"{name}.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def form() -> PageState:
    return golden("acme_search_form")


@pytest.fixture(scope="module")
def detail() -> PageState:
    return golden("acme_record_detail")


@pytest.fixture(scope="module")
def not_found() -> PageState:
    return golden("acme_not_found")


@pytest.fixture(scope="module")
def legacy() -> PageState:
    return golden("legacy_portal")


@pytest.fixture(scope="module")
def search_graph():
    return load_graph("acme_record_search", GRAPHS)


# --- patterns & shapers ---------------------------------------------------------------


def test_pattern_regex_literal_vs_substring() -> None:
    assert Pattern("/^search$/i").test("Search")
    assert not Pattern("/^search$/i").test("Search records")
    assert Pattern("reference number").test("Enter the REFERENCE NUMBER here")
    assert not Pattern("abc").test(None)
    # URL routes start with "/" — `re:` keeps them unambiguous.
    assert Pattern(r"re:^/records/").test("/records/{id}")
    assert not Pattern(r"re:^/records/").test("/search")
    assert Pattern("/search").test("/search ?ref&type")  # plain substring


def test_builtin_shapers_and_registration() -> None:
    assert shapers.shape("upper", "acm-2024-000123") == "ACM-2024-000123"
    assert shapers.shape("digits_only", "ACM-2024-000123") == "2024000123"
    assert shapers.shape("alnum_only", "a-b c") == "abc"
    assert shapers.shape("collapse_spaces", "  a   b ") == "a b"
    with pytest.raises(shapers.ShaperError):
        shapers.shape("nope", "x")

    @shapers.register_shaper("test_reverse")
    def reverse(value: str) -> str:
        return value[::-1]

    try:
        assert shapers.shape("test_reverse", "abc") == "cba"
        with pytest.raises(ValueError, match="already registered"):
            shapers.register_shaper("test_reverse")(lambda v: v)
        with pytest.raises(ValueError, match="snake_case"):
            shapers.register_shaper("Bad-Name")
    finally:
        shapers.SHAPERS.pop("test_reverse")


def test_plugins_register_shapers_at_import(tmp_path, monkeypatch) -> None:
    (tmp_path / "gw_test_plugin.py").write_text(
        "from graphwright.engine.shapers import register_shaper\n"
        "from graphwright.observe.pagestate import register_portal_alias\n"
        "register_shaper('plugin_rot')(lambda v: v[1:] + v[:1])\n"
        "register_portal_alias('records.acme.example', 'acme-records')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert plugins.load("gw_test_plugin, ") == ["gw_test_plugin"]
        assert plugins.load("gw_test_plugin") == []  # once
        assert shapers.shape("plugin_rot", "abc") == "bca"
        from graphwright.observe.pagestate import route_template

        assert route_template("https://records.acme.example/x")[0] == "acme-records"
    finally:
        shapers.SHAPERS.pop("plugin_rot", None)
        from graphwright.observe.pagestate import PORTAL_ALIASES

        PORTAL_ALIASES.pop("records.acme.example", None)


# --- schema ---------------------------------------------------------------------------------


def test_condition_and_action_require_exactly_one_key() -> None:
    with pytest.raises(ValueError):
        Condition()
    with pytest.raises(ValueError):
        Condition(url="/a/", page_type="x")
    with pytest.raises(ValueError):
        Action()
    with pytest.raises(ValueError):
        Action.model_validate({"click": {"target": "a"}, "jump": {"to": "b"}})
    assert Condition.model_validate({"not": {"url": "/x/"}}).kind == "not_"
    assert Condition.model_validate({"request": {"name": "otp", "status": "failed"}}).kind == "request"
    assert Action.model_validate({"assert": {"condition": {"always": True}}}).verb == "assert"
    assert Action.model_validate({"fetch": {"request": "otp"}}).verb == "fetch"


def test_node_and_extract_shape_rules() -> None:
    with pytest.raises(ValueError, match="need `recognize`"):
        Node(transitions=[{"to": "x"}])
    with pytest.raises(ValueError, match="at least one transition"):
        Node(recognize={"always": True})
    with pytest.raises(ValueError, match="terminal nodes carry no"):
        Node(terminal="success", transitions=[{"to": "x"}])
    with pytest.raises(ValueError, match="needs `then`"):
        Node(call="common/login")
    with pytest.raises(ValueError, match="exactly one of `target` or `css`"):
        Action.model_validate({"extract": {"into": "x", "fields": {"a": {"target": "t", "css": "h1"}}}})
    with pytest.raises(ValueError, match="`match` or `header`"):
        Action.model_validate({"extract": {"into": "x", "tables": {"t": {}}}})


@pytest.mark.parametrize("name", EXAMPLE_GRAPHS)
def test_example_graphs_lint_and_round_trip(name: str) -> None:
    wf = Workflow.from_yaml(GRAPHS / f"{name}.yaml")
    assert lint(wf) == []
    text = wf.to_yaml()
    assert Workflow.from_yaml_text(text) == wf
    # Block sequences are indented under their key, the way the graphs are hand-written.
    assert "\nnodes:\n" in text and "\n    actions:\n      - " in text


def test_example_graphs_exist() -> None:
    assert {"acme_record_search", "acme_account_login", "acme_login_relay_otp"} <= set(EXAMPLE_GRAPHS)


# --- lint -------------------------------------------------------------------------------------


def _minimal(**over) -> dict:
    wf = {
        "workflow": "t", "portal": "p", "start": "a",
        "nodes": {
            "a": {"recognize": {"always": True}, "transitions": [{"to": "done"}]},
            "done": {"terminal": "success"},
        },
    }
    wf.update(over)
    return wf


def test_lint_catches_dangling_refs_unreachable_nodes_and_unknown_shapers() -> None:
    wf = Workflow.model_validate(_minimal(nodes={
        "a": {
            "recognize": {"visible": "ghost"},
            "actions": [
                {"click": {"target": "nope"}},
                {"fill": {"target": "nope", "value": "{tax_id}", "shape": "bogus"}},
                {"extract": {"into": "x", "fields": {"f": {"target": "ghost_field"}}}},
                {"extract": {"into": "y"}},
            ],
            "transitions": [{"to": "missing"}],
            "on_fail": "elsewhere",
        },
        "orphan": {"recognize": {"always": True}, "transitions": [{"to": "done"}]},
        "done": {"terminal": "success"},
    }))
    joined = "\n".join(lint(wf))
    assert "unknown node 'missing'" in joined
    assert "on_fail points to unknown node 'elsewhere'" in joined
    assert "unknown target 'ghost'" in joined
    assert "unknown target 'nope'" in joined
    assert "unknown shaper 'bogus'" in joined
    assert "unknown input {tax_id}" in joined
    assert "extract field 'f' references unknown target 'ghost_field'" in joined
    assert "extract needs `fields` or `tables`" in joined
    assert "orphan: unreachable from start" in joined
    with pytest.raises(GraphError):
        compile_workflow(wf)


def test_lint_checks_entry_url_templates_and_credential_inputs() -> None:
    wf = Workflow.model_validate(_minimal(
        entry_url="{base}/login",
        inputs={"password": {"credential": "password", "default": "x"}},
    ))
    joined = "\n".join(lint(wf))
    assert "entry_url references unknown input {base}" in joined
    assert "a credential input cannot have a default" in joined


def test_lint_rejects_literal_credentials_in_fills() -> None:
    wf = Workflow.model_validate(_minimal(
        inputs={"user": {}, "password": {"type": "password"}},
        nodes={
            "a": {
                "recognize": {"always": True},
                "actions": [
                    {"fill": {"target": "user_field", "value": "jane.doe"}},           # literal user → error
                    {"fill": {"target": "password_field", "value": "hunter2"}},        # literal password → error
                    {"fill": {"target": "search", "value": "literal is fine here"}},   # not sensitive → ok
                    {"fill": {"target": "password_field", "value": "{password}"}},     # templated → ok
                ],
                "transitions": [{"to": "done"}],
            },
            "done": {"terminal": "success"},
        },
        targets={
            "user_field": {"description": "username box", "strategies": [{"role": "textbox"}]},
            "password_field": {"description": "password", "strategies": [{"role": "textbox"}]},
            "search": {"description": "search box", "strategies": [{"role": "textbox"}]},
        },
    ))
    errors = lint(wf)
    literal = [e for e in errors if "literal value" in e]
    assert len(literal) == 2
    assert 'value: "{user}"' in literal[0] and 'value: "{password}"' in literal[1]
    assert not any("search" in e for e in errors)


def test_lint_requires_a_reachable_success_terminal() -> None:
    wf = Workflow.model_validate(_minimal(nodes={
        "a": {"recognize": {"always": True}, "transitions": [{"to": "out"}]},
        "out": {"terminal": "outcome", "result": {"status": "x"}},
    }))
    assert "no reachable `terminal: success` node" in lint(wf)


# --- the search graph against its goldens --------------------------------------------------


def test_search_graph_recognizes_only_the_search_form(search_graph, form) -> None:
    m = search_graph["search_form"].recognize(form)
    assert m.ok, m.reasons
    winner, all_matches = search_graph.resync(form)
    assert winner == "search_form"
    assert not all_matches["record_detail"].ok


def test_search_targets_resolve_to_the_real_elements(search_graph, form, detail) -> None:
    report = targets_report(search_graph.workflow.targets, form)
    got = {name: (r.element.id if r.element else None) for name, r in report.items()}
    assert got == {"reference_field": "e4", "record_type": "e5", "search_button": "e6"}
    # Not on the detail page — and the report says why, strategy by strategy.
    missing = targets_report(search_graph.workflow.targets, detail)["reference_field"]
    why = missing.explain(search_graph.workflow.targets["reference_field"])
    assert missing.element is None
    assert any("0 match(es)" in line for line in why)
    assert any("no strategy matched" in line for line in why)


def test_search_transitions_explain_why_nothing_fired_yet(search_graph, form) -> None:
    decision = search_graph["search_form"].next(form)
    assert decision.to is None
    text = "\n".join(decision.explain())
    assert "→ not_found: no" in text and "not found (0 strings)" in text
    assert "→ record_detail: no" in text and "does not match '/search'" in text
    assert "no transition matched" in text


def test_search_transitions_fire_on_the_alert_and_on_navigation(search_graph, not_found, detail) -> None:
    assert search_graph["search_form"].next(not_found).to == "not_found"
    assert search_graph["search_form"].next(detail).to == "record_detail"
    assert search_graph.resync(detail)[0] == "record_detail"
    assert search_graph["record_detail"].recognize(detail).ok


def test_search_graph_edges_for_the_canvas(search_graph) -> None:
    edges = search_graph.edges()
    assert ("search_form", "not_found", "text 're:^no record found'") in edges
    assert ("record_detail", "done", "always") in edges
    assert len([e for e in edges if e[0] == "search_form"]) == 2


def test_login_graphs_recognize_each_step() -> None:
    login, verify, account = golden("acme_login"), golden("acme_verify"), golden("acme_account")
    g = load_graph("acme_account_login", GRAPHS)
    assert g.resync(login)[0] == "sign_in"
    assert g.resync(verify)[0] == "verify"
    assert g.resync(account)[0] == "dashboard"

    relay = load_graph("acme_login_relay_otp", GRAPHS)
    # get_code only recognizes the page while its request has not run…
    assert relay.resync(verify, requests={"otp": "pending"})[0] == "get_code"
    # …once it has, the same page is enter_code's.
    assert relay.resync(verify, requests={"otp": "ok"})[0] == "enter_code"
    assert relay["get_code"].next(verify, vars={"otp": None}, requests={"otp": "failed"}).to == "otp_unavailable"
    assert relay["get_code"].next(verify, vars={"otp": {"code": "1"}}, requests={"otp": "ok"}).to == "enter_code"


def test_dom_id_strategy_matches_the_html_id_offline(form, legacy) -> None:
    """`dom_id` works against a stored PageState (unlike css), so it is usable in recognize."""
    assert [e.id for e in match_strategy(Strategy(dom_id="re:^ref$"), form)] == ["e4"]
    # substring form is case-insensitive
    assert [e.id for e in match_strategy(Strategy(dom_id="casenumber"), legacy)] == ["e4"]
    # a suffix anchor, the way generated (JSF-style) ids are matched
    [doc] = match_strategy(Strategy(role="textbox", dom_id="re:Document$"), legacy)
    assert doc.name == "Party document"
    assert match_strategy(Strategy(dom_id="does-not-exist"), form) == []

    wf = Workflow.model_validate({
        "workflow": "t", "portal": "demo-site", "start": "form",
        "nodes": {
            "form": {"recognize": {"visible": {"dom_id": "re:^ref$"}}, "transitions": [{"to": "done"}]},
            "done": {"terminal": "success"},
        },
    })
    m = compile_workflow(wf)["form"].recognize(form)
    assert m.ok and "e4" in m.reasons[0]


# --- frame-aware predicates against the legacy portal ---------------------------------------


def test_frame_aware_predicates_on_the_legacy_portal(legacy) -> None:
    wf = Workflow.model_validate({
        "workflow": "legacy_probe", "portal": "portal.test", "start": "form",
        "nodes": {
            "form": {
                "recognize": {"all": [
                    {"frame_count": {"min": 2}},
                    {"frame_url": r"re:/child\.html"},
                    {"text": "/no information is available/i", "in": "alerts"},
                    {"visible": {"role": "radio", "name": "/^personal$/i", "frame": 0}},
                    {"visible": {"role": "button", "name": "/^apply$/i", "frame": 1}},
                    {"not": {"visible": {"role": "button", "name": "/^apply$/i", "frame": 0}}},
                ]},
                "transitions": [{"to": "done"}],
            },
            "done": {"terminal": "success"},
        },
    })
    m = compile_workflow(wf)["form"].recognize(legacy)
    assert m.ok, "\n".join(m.reasons)
    assert any("matches ['f1']" in r for r in m.reasons)
