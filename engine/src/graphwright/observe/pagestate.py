"""PageState — the observation contract.

One PageState per observation. It is deliberately small: interactive elements, the
page's signposts (alerts, headings), table *shapes* (not contents), frames and a route
template. Everything that consumes a page (graph predicates, decision models, LLM
repair, goldens) reads this model, never raw HTML.

Element ids (`e12`, `f1e3`) are assigned inside the isolated world and are stable for
the lifetime of the frame, so two observations of an unchanged page produce the same
ids — which is what makes diffs against goldens meaningful.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, Field

# --- route templates -------------------------------------------------------------------

# Path segments that are identifiers, not structure: integers, hex blobs, UUIDs, and
# dashed reference numbers that are mostly digits (ACM-2024-000123, 1234567-89.2024).
_ID_SEGMENT = re.compile(
    r"^(\d+|[0-9a-f]{8,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|(?=(?:\D*\d){6})[A-Za-z0-9]{0,6}[-.]?[\d.\-]+)$",
    re.IGNORECASE,
)

# host → short portal alias; unknown hosts are their own alias (minus "www.").
# Plugins add entries with register_portal_alias().
PORTAL_ALIASES: dict[str, str] = {}


def register_portal_alias(host: str, alias: str) -> None:
    PORTAL_ALIASES[host.lower()] = alias


def portal_alias(host: str | None, aliases: dict[str, str] | None = None) -> str:
    """`records.acme.example` → its registered alias; unknown hosts return the host
    itself without a leading `www.`."""
    if not host:
        return "unknown"
    host = host.lower()
    table = aliases if aliases is not None else PORTAL_ALIASES
    if host in table:
        return table[host]
    return host.removeprefix("www.")


def route_template(url: str, aliases: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (portal, route) where route is the path with ids collapsed plus the
    *names* of query parameters, never their values.

    https://records.acme.example/records/48213/show?ref=ACM-1&tab=events
        → ("records.acme.example", "/records/{id}/show ?ref&tab")
    """
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    collapsed = ["{id}" if _ID_SEGMENT.match(s) else s for s in segments]
    path = "/" + "/".join(collapsed) if collapsed else "/"
    params = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    route = f"{path} ?{'&'.join(params)}" if params else path
    return portal_alias(parts.hostname, aliases), route


# --- models ----------------------------------------------------------------------------


class BBox(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Element(BaseModel):
    id: str  # observation id (e12 / f1e3) — stable for the frame's lifetime only
    frame: int = 0
    role: str
    tag: str
    dom_id: str | None = None  # the HTML `id` attribute, when present
    name: str = ""
    value: str | None = None
    href: str | None = None
    options: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    landmark: str | None = None
    nearby: list[str] = Field(default_factory=list)
    bbox: BBox
    in_viewport: bool = True

    def label(self) -> str:
        """Best human label: accessible name, else nearest text, else the tag."""
        return self.name or (self.nearby[0] if self.nearby else "") or self.tag


class Heading(BaseModel):
    level: int
    text: str
    frame: int = 0


class TableShape(BaseModel):
    id: str
    frame: int = 0
    name: str | None = None
    headers: list[str] = Field(default_factory=list)
    rows: int


class FrameInfo(BaseModel):
    index: int
    url: str
    route: str
    parent: int | None = None


class Stats(BaseModel):
    candidates: int = 0
    hidden: int = 0
    kept: int = 0
    frames: int = 1
    extract_ms: float = 0.0
    total_ms: float = 0.0


class PageState(BaseModel):
    url: str
    portal: str
    route: str
    title: str = ""
    lang: str | None = None
    ready: str = ""
    page_type: str | None = None  # filled by observe/classify.py, not by the extractor
    signature: str | None = None
    frames: list[FrameInfo] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    headings: list[Heading] = Field(default_factory=list)
    elements: list[Element] = Field(default_factory=list)
    tables: list[TableShape] = Field(default_factory=list)
    stats: Stats = Field(default_factory=Stats)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # -- construction --

    @classmethod
    def from_frames(
        cls,
        frames: list[tuple[int, int | None, dict[str, Any]]],
        *,
        aliases: dict[str, str] | None = None,
        total_ms: float = 0.0,
    ) -> PageState:
        """Merge per-frame extractor output. `frames` is [(index, parent_index, raw)],
        index 0 being the main frame."""
        if not frames:
            raise ValueError("at least the main frame is required")
        main = frames[0][2]
        portal, route = route_template(main["url"], aliases)

        infos: list[FrameInfo] = []
        alerts: list[str] = []
        headings: list[Heading] = []
        elements: list[Element] = []
        tables: list[TableShape] = []
        candidates = hidden = 0
        extract_ms = 0.0

        for index, parent, raw in frames:
            _, frame_route = route_template(raw["url"], aliases)
            infos.append(FrameInfo(index=index, url=raw["url"], route=frame_route, parent=parent))
            alerts.extend(a for a in raw.get("alerts", []) if a not in alerts)
            headings.extend(Heading(frame=index, **h) for h in raw.get("headings", []))
            elements.extend(Element(frame=index, **e) for e in raw.get("elements", []))
            tables.extend(TableShape(frame=index, **t) for t in raw.get("tables", []))
            stats = raw.get("stats", {})
            candidates += stats.get("candidates", 0)
            hidden += stats.get("hidden", 0)
            extract_ms += stats.get("extract_ms", 0.0)

        # Reading order: on-screen first, then top-to-bottom, left-to-right.
        elements.sort(key=lambda e: (not e.in_viewport, e.frame, e.bbox.y, e.bbox.x))

        return cls(
            url=main["url"],
            portal=portal,
            route=route,
            title=main.get("title", ""),
            lang=main.get("lang") or None,
            ready=main.get("ready", ""),
            frames=infos,
            alerts=alerts,
            headings=headings,
            elements=elements,
            tables=tables,
            stats=Stats(
                candidates=candidates,
                hidden=hidden,
                kept=len(elements),
                frames=len(frames),
                extract_ms=round(extract_ms, 1),
                total_ms=round(total_ms, 1),
            ),
        )

    # -- queries --

    def by_id(self, element_id: str) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def interactive(self) -> list[Element]:
        return [e for e in self.elements if e.role not in ("heading", "img", "generic")]

    # -- rendering --

    def render(
        self, *, max_elements: int = 60, show_tables: bool = True, collapse_chrome: bool = True
    ) -> str:
        """Compact, token-efficient text for decision models and LLM prompts.

        Plain lines, short ids, no JSON. Links inside site chrome (nav/header/footer/
        aside) collapse to one summary line per landmark when `collapse_chrome` is set;
        they stay in `elements`, so the graph can still target them. Values of task
        inputs should already have been replaced by `{var}` placeholders upstream (see
        repair/bundle.py) — this method does not redact.
        """
        out: list[str] = [f"route: {self.portal} {self.route}"]
        if self.page_type:
            out[0] += f"    page: {self.page_type}"
        if self.title:
            out.append(f"title: {_clip(self.title, 80)}")
        if len(self.frames) > 1:
            populated = {e.frame for e in self.elements} | {h.frame for h in self.headings}
            parts = [f"f{f.index}: {f.route}" for f in self.frames[1:] if f.index in populated]
            empty = len(self.frames) - 1 - len(parts)
            if empty:
                parts.append(f"{empty} empty")
            out.append(f"frames: {len(self.frames)} ({', '.join(parts)})")
        for a in self.alerts:
            out.append(f'ALERT "{_clip(a, 160)}"')
        for h in self.headings[:8]:
            out.append(f"§ h{h.level} {_clip(h.text, 80)}")

        content: list[Element] = []
        chrome: dict[str, list[Element]] = {}
        for e in self.elements:
            kind = _chrome_kind(e)
            if collapse_chrome and kind and e.role == "link":
                chrome.setdefault(kind, []).append(e)
            else:
                content.append(e)

        shown = content[:max_elements]
        for e in shown:
            out.append(_render_element(e))
        rest = len(content) - len(shown)
        if rest > 0:
            offscreen = sum(1 for e in content[max_elements:] if not e.in_viewport)
            out.append(f"(+{rest} more elements, {offscreen} offscreen)")
        for kind, links in chrome.items():
            names = [_clip(link.label(), 24) for link in links[:4]]
            more = f", +{len(links) - 4}" if len(links) > 4 else ""
            out.append(f"{kind}: {len(links)} links ({', '.join(names)}{more})")

        if show_tables:
            for t in self.tables:
                name = f' "{_clip(t.name, 40)}"' if t.name else ""
                heads = ", ".join(t.headers[:8]) if t.headers else "no headers"
                out.append(f"table {t.id}{name} [{heads}] × {t.rows} rows")
        return "\n".join(out)


_CHROME_KINDS = {
    "nav": "nav", "navigation": "nav",
    "header": "header", "banner": "header",
    "footer": "footer", "contentinfo": "footer",
    "aside": "aside", "complementary": "aside",
}


def _chrome_kind(e: Element) -> str | None:
    """`nav#menu` → "nav"; None when the element is not inside site chrome."""
    if not e.landmark:
        return None
    return _CHROME_KINDS.get(e.landmark.split("#", 1)[0])


def _render_element(e: Element) -> str:
    line = f'{e.id} {e.role} "{_clip(e.name, 60)}"' if e.name else f"{e.id} {e.role}"
    if e.dom_id:
        line += f" #{_clip(e.dom_id, 40)}"
    if e.value is not None and e.value != "":
        line += f' = "{_clip(e.value, 40)}"'
    if e.states:
        line += " " + " ".join(f"[{s}]" for s in e.states)
    if e.options:
        shown = ", ".join(_clip(o, 24) for o in e.options[:6])
        more = f" +{len(e.options) - 6}" if len(e.options) > 6 else ""
        line += f" options({shown}{more})"
    if e.nearby:
        line += " — near: " + " | ".join(_clip(n, 40) for n in e.nearby[:2])
    if e.landmark:
        line += f" — in {e.landmark}"
    if e.href and e.role == "link":
        line += f" → {_clip(e.href, 50)}"
    if not e.in_viewport:
        line += " (offscreen)"
    return line


def _clip(text: str | None, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"
