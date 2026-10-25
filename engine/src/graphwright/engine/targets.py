"""Target resolution against a PageState.

Strategies are tried in order; the first one that yields a match wins. Every attempt
is recorded so the builder can show *why* a target did or did not resolve. `css` /
`xpath` strategies need the live DOM and are reported as skipped here — the runtime
resolves those through the isolated world.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graphwright.engine.schema import FrameSpec, Pattern, Strategy, Target
from graphwright.observe.pagestate import Element, PageState


@dataclass
class Attempt:
    strategy_index: int
    matched: list[str]  # element ids
    skipped: str | None = None  # reason, when the strategy could not be evaluated

    def describe(self, strategy: Strategy) -> str:
        crit = ", ".join(
            f"{k}={v!r}" for k, v in strategy.model_dump(exclude_none=True, exclude_defaults=True).items()
        )
        if self.skipped:
            return f"#{self.strategy_index} ({crit}): skipped — {self.skipped}"
        return f"#{self.strategy_index} ({crit}): {len(self.matched)} match(es)" + (
            f" → {self.matched[:5]}" if self.matched else ""
        )


@dataclass
class Resolution:
    target: str
    element: Element | None
    attempts: list[Attempt] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.element is not None

    def explain(self, target: Target) -> list[str]:
        lines = [a.describe(target.strategies[a.strategy_index]) for a in self.attempts]
        if self.error:
            lines.append(self.error)
        return lines


def _frame_matches(spec: FrameSpec, element: Element, state: PageState) -> bool:
    if spec == "any":
        return True
    if isinstance(spec, int):
        return element.frame == spec
    frame = next((f for f in state.frames if f.index == element.frame), None)
    return frame is not None and (Pattern(spec).test(frame.route) or Pattern(spec).test(frame.url))


def match_strategy(strategy: Strategy, state: PageState, *, frame: FrameSpec = "any") -> list[Element]:
    """All elements matching every criterion, in reading order."""
    effective_frame = strategy.frame if strategy.frame != "any" else frame
    name = Pattern(strategy.name) if strategy.name else None
    dom_id = Pattern(strategy.dom_id) if strategy.dom_id else None
    nearby = Pattern(strategy.nearby) if strategy.nearby else None
    landmark = Pattern(strategy.landmark) if strategy.landmark else None
    value = Pattern(strategy.value) if strategy.value else None

    out: list[Element] = []
    for e in state.elements:
        if strategy.role is not None and e.role != strategy.role:
            continue
        if strategy.tag is not None and e.tag != strategy.tag:
            continue
        if name is not None and not name.test(e.name):
            continue
        if dom_id is not None and not dom_id.test(e.dom_id):
            continue
        if nearby is not None and not any(nearby.test(n) for n in e.nearby):
            continue
        if landmark is not None and not landmark.test(e.landmark):
            continue
        if value is not None and not value.test(e.value):
            continue
        if strategy.state is not None and strategy.state not in e.states:
            continue
        if strategy.in_viewport is not None and e.in_viewport != strategy.in_viewport:
            continue
        if not _frame_matches(effective_frame, e, state):
            continue
        out.append(e)
    return out


def resolve(name: str, target: Target, state: PageState) -> Resolution:
    res = Resolution(target=name, element=None)
    for i, strategy in enumerate(target.strategies):
        if strategy.needs_dom:
            res.attempts.append(Attempt(i, [], skipped="needs live DOM (css/xpath)"))
            continue
        matches = match_strategy(strategy, state, frame=target.frame)
        res.attempts.append(Attempt(i, [m.id for m in matches]))
        if not matches:
            continue
        if target.unique and len(matches) > 1:
            res.error = f"target {name!r} is unique but strategy #{i} matched {len(matches)} elements"
            continue
        if strategy.index >= len(matches):
            res.error = f"strategy #{i} asked for index {strategy.index} but matched {len(matches)}"
            continue
        res.element = matches[strategy.index]
        res.error = None
        break
    if res.element is None and res.error is None:
        res.error = f"target {name!r}: no strategy matched"
    return res


def resolve_inline(strategy: Strategy, state: PageState) -> list[Element]:
    return match_strategy(strategy, state)
