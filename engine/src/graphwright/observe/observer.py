"""Observer — fills a PageState from a live Page.

Frame-aware: every frame in the tree gets its own isolated world; element ids are
prefixed per frame (`e3` main, `f1e3` first child frame) and `box()` translates child
frame geometry into main-frame viewport coordinates by walking `frameOffset()` up the
parent chain.

Cost model: the first observation on a fresh page pays for world creation plus library
install (one extra round trip per frame). Later observations are one `Runtime.evaluate`
per frame. `signature()` is a single cheap call on the main frame and is what the
engine polls while waiting for a transition.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graphwright.browser.cdp import FrameRef, IsolatedWorlds
from graphwright.observe.pagestate import PageState

_LIB_PATH = Path(__file__).with_name("pagestate.js")
_LIB_SOURCE = _LIB_PATH.read_text(encoding="utf-8")


@dataclass(frozen=True)
class ElementBox:
    id: str
    frame: int
    x: float  # main-frame viewport coordinates
    y: float
    w: float
    h: float
    visible: bool
    in_viewport: bool
    occluded: bool | None
    disabled: bool

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)


class Observer:
    def __init__(
        self,
        page: Any,
        *,
        max_elements: int = 2000,
        portal_aliases: dict[str, str] | None = None,
    ) -> None:
        self.page = page
        self._worlds = IsolatedWorlds(page)
        self._max_elements = max_elements
        self._aliases = portal_aliases
        self._installed: dict[str, int] = {}  # frame id -> generation the lib was installed in
        self._frame_index: dict[str, int] = {}  # frame id -> stable index while the frame lives
        self._frames: list[FrameRef] = []

    # -- internals --

    async def _refresh_frames(self) -> list[FrameRef]:
        frames = await self._worlds.frames()
        live = {f.id for f in frames}
        # Keep indices stable for frames we have seen; forget dead ones.
        self._frame_index = {fid: idx for fid, idx in self._frame_index.items() if fid in live}
        self._frame_index.setdefault(frames[0].id, 0)
        next_idx = max(self._frame_index.values(), default=0) + 1
        for f in frames[1:]:
            if f.id not in self._frame_index:
                self._frame_index[f.id] = next_idx
                next_idx += 1
        self._frames = frames
        return frames

    async def _ensure_lib(self, frame_id: str) -> None:
        gen = self._worlds.generation
        if self._installed.get(frame_id) == gen:
            # Context may have been recreated without a generation bump (first touch).
            return
        await self._worlds.context_for(frame_id)
        await self._worlds.evaluate(frame_id, _LIB_SOURCE)
        self._installed[frame_id] = self._worlds.generation

    async def _call(self, frame_id: str, expr: str) -> Any:
        await self._ensure_lib(frame_id)
        try:
            return await self._worlds.evaluate(frame_id, expr)
        except Exception as exc:
            # If the world was recreated under us, __ps is gone: reinstall once.
            if "__ps" in str(exc):
                self._installed.pop(frame_id, None)
                await self._ensure_lib(frame_id)
                return await self._worlds.evaluate(frame_id, expr)
            raise

    @staticmethod
    def _prefix(index: int) -> str:
        return "" if index == 0 else f"f{index}"

    # -- public API --

    def set_max_elements(self, limit: int) -> None:
        self._max_elements = limit

    async def observe(self) -> PageState:
        t0 = time.perf_counter()
        frames = await self._refresh_frames()
        opts = json.dumps({"maxElements": self._max_elements})
        results: list[tuple[int, int | None, dict[str, Any]]] = []
        for f in frames:
            idx = self._frame_index[f.id]
            parent = self._frame_index.get(f.parent_id) if f.parent_id else None
            raw = await self._call(f.id, f"__ps.extract({opts}, {json.dumps(self._prefix(idx))})")
            if raw is None:  # about:blank or a frame that died mid-walk
                continue
            results.append((idx, parent, raw))
        results.sort(key=lambda r: r[0])
        state = PageState.from_frames(
            results, aliases=self._aliases, total_ms=(time.perf_counter() - t0) * 1000
        )
        state.signature = await self.signature()
        return state

    async def signature(self) -> str:
        frames = self._frames or await self._refresh_frames()
        sig = await self._call(frames[0].id, "__ps.signature()")
        return f"{sig}:{len(frames)}"

    async def wait_for_change(
        self,
        baseline: str,
        timeout_s: float,
        *,
        polls: tuple[float, ...] = (0.05, 0.1, 0.2, 0.4),
    ) -> bool:
        """Poll the signature with backoff until it differs from `baseline`.

        Returns True on change, False on timeout. The engine uses this instead of
        fixed sleeps or `networkidle`.
        """
        deadline = time.perf_counter() + timeout_s
        i = 0
        while time.perf_counter() < deadline:
            await asyncio.sleep(polls[min(i, len(polls) - 1)])
            i += 1
            try:
                await self._refresh_frames()
                if await self.signature() != baseline:
                    return True
            except Exception:
                # Mid-navigation the world may be gone for a moment; that is a change.
                return True
        return False

    async def box(self, element_id: str) -> ElementBox | None:
        """Geometry for one element in main-frame coordinates, with occlusion info."""
        frames = self._frames or await self._refresh_frames()
        index = 0
        if element_id.startswith("f"):
            index = int(element_id[1 : element_id.index("e")])
        frame = next((f for f in frames if self._frame_index.get(f.id) == index), None)
        if frame is None:
            return None
        raw = await self._call(frame.id, f"__ps.box({json.dumps(element_id)})")
        if raw is None:
            return None
        ox = oy = 0.0
        node = frame
        while node.parent_id:
            off = await self._call(node.id, "__ps.frameOffset()")
            if not off:
                break
            ox += off["x"]
            oy += off["y"]
            node = next(f for f in frames if f.id == node.parent_id)
        b = raw["bbox"]
        return ElementBox(
            id=element_id,
            frame=index,
            x=b["x"] + ox,
            y=b["y"] + oy,
            w=b["w"],
            h=b["h"],
            visible=raw["visible"],
            in_viewport=raw["in_viewport"],
            occluded=raw["occluded"],
            disabled=raw["disabled"],
        )

    # -- runtime helpers (used by engine/dsl.py) --

    async def capture_image(self, element_id: str) -> dict[str, Any]:
        """Browser-rendered crop of a registered element, independent of DOM src.

        CDP addresses the actual node, including frames, CSS composites and tainted
        canvases. No image fetching, canvas decoding, or page-world code is used.
        """
        frame = self._frame_for(element_id)
        if frame is None:
            raise ValueError("image frame is gone")
        await self._ensure_lib(frame.id)
        client = await self._worlds.session()
        remote = await client.send("Runtime.evaluate", {
            "expression": f"__ps.captureElement({json.dumps(element_id)})",
            "contextId": await self._worlds.context_for(frame.id),
            "returnByValue": False,
        })
        object_id = remote.get("result", {}).get("objectId")
        if not object_id:
            raise ValueError("element is missing or hidden")
        try:
            await client.send("DOM.scrollIntoViewIfNeeded", {"objectId": object_id})
            error = await self._call(frame.id, f"__ps.captureCheck({json.dumps(element_id)})")
            if error:
                raise ValueError(error)
            # Chromium's box model is in main-frame viewport coordinates, also
            # for a node inside an iframe. Do not guess offsets from DOM src URLs.
            model = await client.send("DOM.getBoxModel", {"objectId": object_id})
            quad = model["model"]["border"]
            x, y = math.floor(min(quad[::2])), math.floor(min(quad[1::2]))
            width = math.ceil(max(quad[::2])) - x
            height = math.ceil(max(quad[1::2])) - y
            if width <= 0 or height <= 0 or width * height > 2_000_000:
                raise ValueError("capture exceeds pixel limit or is empty")
            viewport = await self._call(self._frames[0].id,
                                        "({w: innerWidth, h: innerHeight})")
            if x < 0 or y < 0 or x + width > viewport["w"] or y + height > viewport["h"]:
                raise ValueError("element is clipped by the main viewport")
            pixels = await self.page.screenshot(
                type="png", scale="css", timeout=5000,
                clip={"x": x, "y": y, "width": width, "height": height},
            )
            # A relayout during screenshot preparation must not silently crop a
            # different region. Image freshness is checked separately by hash.
            after = await client.send("DOM.getBoxModel", {"objectId": object_id})
            if after["model"]["border"] != quad:
                raise ValueError("element moved during capture; observe and retry")
            data_url = "data:image/png;base64," + base64.b64encode(pixels).decode("ascii")
            if len(data_url) > 4_000_000:
                raise ValueError("capture exceeds byte limit")
            return {"data_url": data_url, "width": width, "height": height,
                    "sha256": hashlib.sha256(pixels).hexdigest(), "mime_type": "image/png"}
        except ValueError:
            raise
        except Exception:
            raise ValueError("browser capture failed; the element or frame may have changed") from None
        finally:
            with suppress(Exception):  # navigation can destroy the remote handle
                await client.send("Runtime.releaseObject", {"objectId": object_id})

    def _frame_for(self, element_id: str) -> FrameRef | None:
        index = 0
        if element_id.startswith("f"):
            index = int(element_id[1 : element_id.index("e")])
        return next((f for f in self._frames if self._frame_index.get(f.id) == index), None)

    async def query(self, selector: str, *, frame: int | None = None, max_results: int = 20) -> list[str]:
        """Resolve a css/xpath selector in the live DOM; returns addressable element ids
        (prefixed per frame). `frame=None` searches every frame, main frame first."""
        frames = self._frames or await self._refresh_frames()
        out: list[str] = []
        for f in frames:
            idx = self._frame_index[f.id]
            if frame is not None and idx != frame:
                continue
            # make sure new ids get the right prefix even before an extract() ran there
            await self._call(f.id, f"__ps.extract({{maxElements: 0}}, {json.dumps(self._prefix(idx))})")
            ids = await self._call(f.id, f"__ps.query({json.dumps(selector)}, {max_results})")
            out.extend(ids or [])
        return out

    async def select_option(self, element_id: str, pattern: str) -> dict[str, Any]:
        frame = self._frame_for(element_id)
        if frame is None:
            return {"ok": False, "reason": "frame not found"}
        return await self._call(frame.id, f"__ps.selectOption({json.dumps(element_id)}, {json.dumps(pattern)})")

    async def scroll_into_view(self, element_id: str) -> bool:
        frame = self._frame_for(element_id)
        return bool(frame and await self._call(frame.id, f"__ps.scrollIntoView({json.dumps(element_id)})"))

    async def value_of(self, element_id: str) -> str | None:
        frame = self._frame_for(element_id)
        return await self._call(frame.id, f"__ps.valueOfId({json.dumps(element_id)})") if frame else None

    async def text_of(self, element_id: str) -> str | None:
        frame = self._frame_for(element_id)
        return await self._call(frame.id, f"__ps.textOfId({json.dumps(element_id)})") if frame else None

    async def table_rows(self, table_id: str, *, max_rows: int = 500) -> dict[str, Any] | None:
        """``{"headers": [...], "rows": [{header: text}, ...]}`` for a table id from a PageState."""
        frame = self._frame_for(table_id)
        return await self._call(frame.id, f"__ps.tableRows({json.dumps(table_id)}, {int(max_rows)})") if frame else None

    async def mask_value(self, element_id: str) -> bool:
        """Show bullets instead of a field's value (evidence screenshots)."""
        frame = self._frame_for(element_id)
        return bool(frame and await self._call(frame.id, f"__ps.maskValueOfId({json.dumps(element_id)})"))

    async def evaluate(self, body: str, args: Any = None, *, world: str = "isolated", frame: int | str = 0) -> Any:
        """Run an async function body with `args` in a frame and return its JSON value.

        isolated: the frame's CDP isolated world (DOM access, invisible to the page).
        main: the page's own JS context, through Playwright (sees page globals)."""
        frames = await self._refresh_frames()
        target = self._pick_frame(frames, frame)
        if target is None:
            raise LookupError(f"no frame matches {frame!r}")
        payload = args if args is not None else {}
        if world == "isolated":
            expr = f"(async (args) => {{ {body}\n}})({json.dumps(payload)})"
            return await self._worlds.evaluate(target.id, expr)
        pw_frame = self._playwright_frame(target)
        if pw_frame is None:
            raise LookupError(f"frame {frame!r} is not reachable from Playwright")
        return await pw_frame.evaluate(f"async (args) => {{ {body}\n}}", payload)

    def _pick_frame(self, frames: list[FrameRef], spec: int | str) -> FrameRef | None:
        if isinstance(spec, int):
            return next((f for f in frames if self._frame_index.get(f.id) == spec), None)
        from graphwright.engine.schema import Pattern
        from graphwright.observe.pagestate import route_template

        p = Pattern(spec)
        return next((f for f in frames if p.test(f.url) or p.test(route_template(f.url)[1])), None)

    def _playwright_frame(self, target: FrameRef) -> Any:
        if self._frame_index.get(target.id) == 0:
            return self.page.main_frame
        return next((f for f in self.page.frames if f.url == target.url), None)

    async def close(self) -> None:
        await self._worlds.close()
