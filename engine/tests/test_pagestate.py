"""Pure-Python tests for the PageState contract: route templates, frame merging and
the compact rendering. No browser involved."""

from __future__ import annotations

from graphwright.observe.pagestate import PORTAL_ALIASES, PageState, portal_alias, register_portal_alias, route_template


def test_route_template_collapses_ids_and_keeps_param_names_only() -> None:
    portal, route = route_template("https://records.acme.example/records/48213/show?ref=ACM-1&tab=events")
    assert portal == "records.acme.example"
    assert route == "/records/{id}/show ?ref&tab"


def test_route_template_collapses_numeric_hex_uuid_and_reference_segments() -> None:
    _, route = route_template(
        "https://portal.example/app/case/Detail/"
        "12345/0a1b2c3d4e5f/ACM-2024-000123/1234567-89.2024.8.26.0100/"
        "123e4567-e89b-12d3-a456-426614174000/listView.seam"
    )
    assert route == "/app/case/Detail/{id}/{id}/{id}/{id}/{id}/listView.seam"
    # structure words with a digit or two are not ids
    assert route_template("https://x.example/v2/api/b2c")[1] == "/v2/api/b2c"


def test_portal_alias_registry_then_host() -> None:
    assert portal_alias("www.example.com") == "example.com"
    assert portal_alias(None) == "unknown"
    register_portal_alias("Records.Acme.Example", "acme-records")
    try:
        assert portal_alias("records.acme.example") == "acme-records"
        assert portal_alias("records.acme.example", aliases={}) == "records.acme.example"
    finally:
        PORTAL_ALIASES.pop("records.acme.example")


def _raw(url: str, **over) -> dict:
    base = {
        "url": url,
        "title": "Search",
        "lang": "en",
        "ready": "complete",
        "elements": [],
        "headings": [],
        "alerts": [],
        "tables": [],
        "stats": {"candidates": 0, "hidden": 0, "kept": 0, "extract_ms": 1.0},
    }
    base.update(over)
    return base


def _el(id: str, role: str, name: str = "", y: int = 0, in_viewport: bool = True, **over) -> dict:
    e = {
        "id": id, "role": role, "tag": "input", "name": name, "value": None, "href": None,
        "options": [], "states": [], "landmark": None, "nearby": [],
        "bbox": {"x": 10, "y": y, "w": 100, "h": 20}, "in_viewport": in_viewport,
    }
    e.update(over)
    return e


def test_from_frames_merges_and_orders_elements_by_viewport_then_position() -> None:
    main = _raw(
        "https://portal.example/cases/open.do",
        elements=[
            _el("e2", "button", "Search", y=300),
            _el("e1", "textbox", "Case number", y=100),
            _el("e3", "link", "Footer", y=2000, in_viewport=False),
        ],
        alerts=["Required field"],
        stats={"candidates": 10, "hidden": 4, "kept": 3, "extract_ms": 3.2},
    )
    child = _raw(
        "https://portal.example/cases/frame.do?x=1",
        elements=[_el("f1e1", "checkbox", "Unified", y=50)],
        alerts=["Required field"],  # duplicate across frames is kept once
        stats={"candidates": 2, "hidden": 0, "kept": 1, "extract_ms": 0.8},
    )

    state = PageState.from_frames([(0, None, main), (1, 0, child)], total_ms=12.0)

    assert state.portal == "portal.example"
    assert state.route == "/cases/open.do"
    assert [f.index for f in state.frames] == [0, 1]
    assert state.frames[1].parent == 0
    assert state.frames[1].route == "/cases/frame.do ?x"
    assert state.alerts == ["Required field"]
    assert [e.id for e in state.elements] == ["e1", "e2", "f1e1", "e3"]
    assert state.elements[2].frame == 1
    assert state.stats.candidates == 12 and state.stats.hidden == 4 and state.stats.kept == 4
    assert state.stats.frames == 2
    assert state.stats.extract_ms == 4.0
    assert state.by_id("f1e1") is not None and state.by_id("nope") is None


def test_render_is_compact_and_line_oriented() -> None:
    main = _raw(
        "https://portal.example/cases/open.do",
        headings=[{"level": 1, "text": "Case lookup — first instance"}],
        alerts=["No information is available for the given parameters."],
        elements=[
            _el("e1", "combobox", "Search by", y=100, value="Case number",
                options=["Case number", "Party name", "Party document"],
                landmark="form#lookup"),
            _el("e2", "textbox", "", y=120, nearby=["Case number"], states=["required"]),
            _el("e3", "button", "Search", y=160),
            _el("e4", "link", "New lookup", y=900, in_viewport=False, href="/cases/open.do"),
        ],
        tables=[{"id": "e9", "name": "Cases", "headers": ["Number", "Class"], "rows": 12}],
    )
    state = PageState.from_frames([(0, None, main)])
    state.page_type = "search_form"

    lines = state.render(max_elements=3).splitlines()

    assert lines[0] == "route: portal.example /cases/open.do    page: search_form"
    assert 'ALERT "No information is available for the given parameters."' in lines
    assert "§ h1 Case lookup — first instance" in lines
    e1 = next(line for line in lines if line.startswith("e1 "))
    assert e1.startswith('e1 combobox "Search by" = "Case number"')
    assert "options(" in e1 and "in form#lookup" in e1
    assert any(line.startswith("e2 textbox [required] — near: Case number") for line in lines)
    assert "(+1 more elements, 1 offscreen)" in lines
    assert 'table e9 "Cases" [Number, Class] × 12 rows' in lines
    assert "{" not in "\n".join(lines) and "}" not in "\n".join(lines)  # no JSON noise


def test_render_collapses_chrome_links_and_empty_frames() -> None:
    main = _raw(
        "https://portal.example/cases/open.do",
        elements=[
            _el("e1", "textbox", "Number", y=100, landmark="form#lookup"),
            _el("e2", "textbox", "Quick search", y=10, landmark="header"),  # non-link chrome stays
            *[_el(f"e{i}", "link", f"Menu {i}", y=1000 + i, in_viewport=False, landmark="nav#menu")
              for i in range(10, 16)],
            _el("e30", "link", "Terms", y=3000, in_viewport=False, landmark="footer"),
        ],
    )
    spacer = _raw("https://portal.example/img/spacer.gif")
    state = PageState.from_frames([(0, None, main), (1, 0, spacer)])

    lines = state.render().splitlines()
    assert "frames: 2 (1 empty)" in lines
    assert any(line.startswith("e2 textbox") for line in lines)
    assert not any(line.startswith("e1") and "Menu" in line for line in lines)
    assert "nav: 6 links (Menu 10, Menu 11, Menu 12, Menu 13, +2)" in lines
    assert "footer: 1 links (Terms)" in lines
    assert len(state.elements) == 9  # collapsing is render-only

    assert "nav:" not in state.render(collapse_chrome=False)


def test_element_label_prefers_name_then_nearby_then_tag() -> None:
    main = _raw(
        "https://portal.example/",
        elements=[
            _el("e1", "textbox", "Name"),
            _el("e2", "textbox", "", nearby=["Tax ID"]),
            _el("e3", "textbox", ""),
        ],
    )
    state = PageState.from_frames([(0, None, main)])
    assert [e.label() for e in state.elements] == ["Name", "Tax ID", "input"]
