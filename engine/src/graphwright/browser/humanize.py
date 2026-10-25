"""Coordinate-based human-shaped input for the graph engine.

Differences from `actions.py` (which the legacy runner still uses):
- mouse position lives here, per page, not in a `window.__rpa_*` global the site
  can read — and it survives navigations;
- every click goes through an actionability check the caller performs with
  `Observer.box()` (visible, in viewport, not occluded, not disabled) before the
  pointer moves;
- movement time follows the distance (Fitts-like), with an overshoot-and-correct on
  longer moves; typing has per-character cadence.

A `StealthBudget` scales all pauses so `open` portals run fast and `protected` ones
look like a person.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StealthBudget:
    scale: float = 1.0  # multiplies every pause; 0.2 is "fast dev loop"
    min_delay_ms: float = 20.0

    @classmethod
    def for_risk(cls, risk: str) -> StealthBudget:
        return cls(scale=1.0) if risk == "protected" else cls(scale=0.35)


@dataclass
class MouseState:
    x: float = field(default_factory=lambda: random.uniform(200, 600))
    y: float = field(default_factory=lambda: random.uniform(150, 400))


class Humanizer:
    def __init__(self, page: Any, budget: StealthBudget | None = None) -> None:
        self.page = page
        self.budget = budget or StealthBudget()
        self.mouse = MouseState()

    # -- timing --

    async def pause(self, mean_ms: float, sigma_ms: float | None = None) -> None:
        sigma_ms = mean_ms / 3 if sigma_ms is None else sigma_ms
        ms = max(self.budget.min_delay_ms, random.gauss(mean_ms, sigma_ms)) * self.budget.scale
        await asyncio.sleep(ms / 1000.0)

    # -- pointer --

    async def move_to(self, x: float, y: float) -> None:
        sx, sy = self.mouse.x, self.mouse.y
        dist = math.hypot(x - sx, y - sy)
        if dist < 1:
            return
        # Fitts-ish: longer moves take longer but sub-linearly; 25–45 micro-steps.
        steps = max(8, min(45, int(12 + dist / 40)))
        cx1 = sx + (x - sx) * random.uniform(0.2, 0.4) + random.uniform(-40, 40)
        cy1 = sy + (y - sy) * random.uniform(0.2, 0.4) + random.uniform(-40, 40)
        cx2 = sx + (x - sx) * random.uniform(0.6, 0.85) + random.uniform(-20, 20)
        cy2 = sy + (y - sy) * random.uniform(0.6, 0.85) + random.uniform(-20, 20)
        # Overshoot the target slightly on long moves, then correct.
        over = dist > 250 and random.random() < 0.6
        tx = x + (random.uniform(4, 14) * (1 if x > sx else -1) if over else 0)
        ty = y + (random.uniform(2, 8) * (1 if y > sy else -1) if over else 0)
        for i in range(1, steps + 1):
            t = i / steps
            omt = 1 - t
            px = omt**3 * sx + 3 * omt**2 * t * cx1 + 3 * omt * t**2 * cx2 + t**3 * tx
            py = omt**3 * sy + 3 * omt**2 * t * cy1 + 3 * omt * t**2 * cy2 + t**3 * ty
            await self.page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.004, 0.012) * max(self.budget.scale, 0.3))
        if over:
            for i in range(1, 5):
                t = i / 4
                await self.page.mouse.move(tx + (x - tx) * t, ty + (y - ty) * t)
                await asyncio.sleep(random.uniform(0.008, 0.02))
        self.mouse.x, self.mouse.y = x, y

    async def click_at(self, x: float, y: float) -> None:
        await self.move_to(x, y)
        await self.pause(90, 30)
        await self.page.mouse.down()
        await asyncio.sleep(random.uniform(0.04, 0.11))
        await self.page.mouse.up()
        await self.pause(140, 50)

    @staticmethod
    def jitter(cx: float, cy: float, w: float, h: float) -> tuple[float, float]:
        """A point inside the box, biased toward the centre, never on the edge."""
        jx = random.gauss(0, max(1.0, w / 8))
        jy = random.gauss(0, max(1.0, h / 8))
        return (
            min(max(cx + jx, cx - w / 2 + 2), cx + w / 2 - 2),
            min(max(cy + jy, cy - h / 2 + 2), cy + h / 2 - 2),
        )

    # -- keyboard --

    async def type_text(self, text: str) -> None:
        for ch in text:
            await self.page.keyboard.type(ch)
            # digraph variance: punctuation and digit runs are slower than letter runs
            mean = 110 if ch.isalpha() else 150
            await self.pause(mean, 45)

    async def clear_field(self) -> None:
        await self.page.keyboard.press("Control+A")
        await self.pause(60, 20)
        await self.page.keyboard.press("Backspace")
        await self.pause(80, 25)

    async def press(self, key: str) -> None:
        await self.page.keyboard.press(key)
        await self.pause(120, 40)

    # -- scrolling --

    async def wheel(self, delta_y: int) -> None:
        direction = 1 if delta_y >= 0 else -1
        remaining = abs(delta_y)
        while remaining > 0:
            chunk = min(remaining, random.randint(80, 160))
            await self.page.mouse.wheel(0, direction * chunk)
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.04, 0.12) * max(self.budget.scale, 0.3))
        await self.pause(150, 50)
