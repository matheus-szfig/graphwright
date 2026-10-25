"""Secrets never print; the redactor catches what does; the fill verb's error text
never quotes a sensitive value."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import graphwright.engine.dsl as dsl
from graphwright.engine import Workflow
from graphwright.engine.dsl import ActionContext, do_fill
from graphwright.engine.errors import StructuralError
from graphwright.engine.schema import Fill
from graphwright.engine.secrets import MASK, Redactor, Secret, SecretInputs


def test_secret_never_prints() -> None:
    s = Secret("hunter2")
    assert str(s) == MASK and repr(s) == "Secret(***)" and f"{s}" == MASK and f"{s!r}" == "Secret(***)"
    assert s.reveal() == "hunter2"
    calls = []
    lazy = Secret(lambda: calls.append(1) or f"code{len(calls)}")
    assert lazy.reveal() == "code1" and lazy.reveal() == "code2"  # fresh on every reveal
    inputs = SecretInputs({"reference": "123", "password": s})
    assert isinstance(inputs, dict) and "hunter2" not in repr(inputs)
    assert inputs.plain() == {"reference": "123"}


def test_redactor_scrubs_nested_payloads() -> None:
    r = Redactor(["hunter2", "jane.doe@example.com"])
    r.add("x")  # too short to scrub — would hit every string
    payload = {"msg": "typed hunter2 for jane.doe@example.com", "nested": ["hunter2", 3, {"k": "ok"}], "s": Secret("q")}
    assert r(payload) == {"msg": f"typed {MASK} for {MASK}", "nested": [MASK, 3, {"k": "ok"}], "s": MASK}
    assert r.text("nothing here") == "nothing here"
    # longest first: a value containing another is replaced whole
    r2 = Redactor(["abcd", "abcdefgh"])
    assert r2.text("xx abcdefgh yy") == f"xx {MASK} yy"


def test_render_template_reveals_only_when_asked() -> None:
    wf = Workflow.model_validate({
        "workflow": "t", "portal": "p", "start": "a", "inputs": {"password": {}, "user": {"pii": False}},
        "nodes": {"a": {"terminal": "success"}},
    })
    ctx = ActionContext(page=None, observer=None, human=None, workflow=wf,  # type: ignore[arg-type]
                        inputs=SecretInputs({"password": Secret("hunter2"), "user": "jane"}))
    assert ctx.render_template("{user}:{password}") == "jane:hunter2"
    assert ctx.render_template("{user}:{password}", reveal=False) == f"jane:{MASK}"


async def test_fill_mismatch_error_hides_pii_values(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = Workflow.model_validate({
        "workflow": "t", "portal": "p", "start": "a",
        "inputs": {"password": {}, "reference": {"pii": False}},
        "nodes": {"a": {"terminal": "success"}},
        "targets": {"pw": {"description": "password", "strategies": [{"css": "#pw"}]}},
    })
    observer = MagicMock()
    observer.value_of = AsyncMock(return_value="garbage")
    human = MagicMock()
    human.click_at = AsyncMock()
    human.clear_field = AsyncMock()
    human.type_text = AsyncMock()
    human.jitter = MagicMock(return_value=(0, 0))
    events: list[dict] = []
    ctx = ActionContext(page=MagicMock(), observer=observer, human=human, workflow=wf,
                        inputs=SecretInputs({"password": Secret("hunter2"), "reference": "0001"}),
                        log=lambda kind, **data: events.append({"kind": kind, **data}))

    async def fake_locate(name, _ctx):
        return "e1", []

    async def fake_box(element_id, _ctx, **_):
        return MagicMock(center=(1, 1), w=1, h=1)

    monkeypatch.setattr(dsl, "locate", fake_locate)
    monkeypatch.setattr(dsl, "actionable_box", fake_box)
    with pytest.raises(StructuralError) as exc:
        await do_fill(Fill(target="pw", value="{password}"), ctx)
    assert "hunter2" not in str(exc.value) and "<7 chars>" in str(exc.value)
    human.type_text.assert_awaited_with("hunter2")  # the page still got the real value

    observer.value_of = AsyncMock(return_value="0001")
    await do_fill(Fill(target="pw", value="{reference}"), ctx)
    assert events[-1]["kind"] == "fill" and events[-1]["value"] == "0001"
