"""CDP plumbing: isolated JavaScript worlds, one per frame.

Why not `page.evaluate`: it runs in the page's main world, so anything we define is
visible to the site's scripts, and stock Playwright keeps `Runtime.enable` on, which is
itself a known automation signal. Here we open our own CDP session, create an isolated
world per frame with `Page.createIsolatedWorld`, and call `Runtime.evaluate` with that
context id. `Runtime.evaluate` does not require `Runtime.enable`.

Worlds die with their frame on navigation; `context_for` recreates them lazily and
callers re-install any library code when the context id changes.

Cross-origin iframes rendered out-of-process (OOPIFs) are not reachable from the page's
session. The browser is launched with `--disable-features=IsolateOrigins,site-per-process`
(see pool.py), which keeps all frames in one process; if that flag is ever dropped, those
frames will simply be missing from `frames()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_WORLD_NAME = "rpa-observe"


class CdpError(RuntimeError):
    """A CDP call failed or the evaluated expression threw."""


@dataclass(frozen=True)
class FrameRef:
    id: str
    url: str
    parent_id: str | None


class IsolatedWorlds:
    def __init__(self, page: Any) -> None:
        self._page = page
        self._session: Any = None
        self._contexts: dict[str, int] = {}  # frame id -> execution context id
        self._generation = 0  # bumped whenever contexts are invalidated

    @property
    def generation(self) -> int:
        """Changes when worlds were recreated — callers use it to re-install code."""
        return self._generation

    async def session(self) -> Any:
        if self._session is None:
            self._session = await self._page.context.new_cdp_session(self._page)
            await self._session.send("Page.enable")
            self._page.on("framenavigated", self._invalidate)
        return self._session

    def _invalidate(self, *_: Any) -> None:
        if self._contexts:
            self._contexts.clear()
            self._generation += 1

    async def frames(self) -> list[FrameRef]:
        """Depth-first frame tree, main frame first."""
        session = await self.session()
        tree = await session.send("Page.getFrameTree")
        out: list[FrameRef] = []

        def walk(node: dict[str, Any], parent: str | None) -> None:
            frame = node["frame"]
            out.append(FrameRef(id=frame["id"], url=frame.get("url", ""), parent_id=parent))
            for child in node.get("childFrames", []):
                walk(child, frame["id"])

        walk(tree["frameTree"], None)
        return out

    async def context_for(self, frame_id: str) -> int:
        cid = self._contexts.get(frame_id)
        if cid is None:
            session = await self.session()
            result = await session.send(
                "Page.createIsolatedWorld",
                {"frameId": frame_id, "worldName": _WORLD_NAME, "grantUniveralAccess": False},
            )
            cid = int(result["executionContextId"])
            self._contexts[frame_id] = cid
        return cid

    async def evaluate(self, frame_id: str, expression: str, *, _retry: bool = True) -> Any:
        """Evaluate in the frame's isolated world; returns the JSON value."""
        session = await self.session()
        cid = await self.context_for(frame_id)
        try:
            result = await session.send(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "contextId": cid,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            )
        except Exception as exc:  # playwright raises a generic Error for protocol failures
            if _retry and "context" in str(exc).lower():
                # The world died (navigation raced us). Recreate once.
                self._invalidate()
                return await self.evaluate(frame_id, expression, _retry=False)
            raise CdpError(f"Runtime.evaluate failed: {exc}") from exc

        details = result.get("exceptionDetails")
        if details:
            text = details.get("exception", {}).get("description") or details.get("text", "")
            raise CdpError(f"script threw: {text}")
        return result.get("result", {}).get("value")

    async def close(self) -> None:
        if self._session is not None:
            try:
                await self._session.detach()
            except Exception:  # session may already be gone with the page
                log.debug("cdp session detach failed", exc_info=True)
            self._session = None
            self._contexts.clear()
