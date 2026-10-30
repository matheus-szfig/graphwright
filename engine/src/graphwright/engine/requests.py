"""Graph requests — values fetched at the moment an action needs them.

A graph declares named requests (``Workflow.requests``); actions use them two ways:

- lazily, in any action template: ``fill: {target: otp, value: "{requests.otp.code}"}``
  runs the request right before typing, every time (``cache: none``), because a 2FA
  code fetched at the start of a run may have expired by the time the form shows up;
- explicitly, with ``fetch: {request: otp, into: otp, on_error: continue}``, which
  stores the values in ``vars`` and records the outcome so a transition can route on
  ``request: {name: otp, status: failed}``.

Every call returns a ``RequestResult`` carrying one ``AttemptTrace`` per try — the
rendered request with secrets masked, status, timing, a body excerpt and what was
extracted. That trace is what the runtime emits as a ``request`` event and what the
builder's Requests tab and ``graphwright request-test`` show.

Kinds: ``http`` (httpx), ``totp`` (RFC 6238 from a template secret, or the run's
injected callback), ``imap`` (poll a mailbox for a recent message). Nothing here
knows about nodes; the runtime and dsl own that.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import json as jsonlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from graphwright.engine.errors import EngineError
from graphwright.engine.schema import Extractor, RequestSpec
from graphwright.engine.secrets import MASK, Secret
from graphwright.engine.totp import seconds_left, totp

_BODY_EXCERPT = 2000
_TOTP_MIN_VALIDITY_S = 3.0  # wait for the next window rather than type a code about to expire
_SENSITIVE_HEADER = re.compile(r"(authorization|cookie|token|key|secret|password|session)", re.IGNORECASE)
_KEEP_RESPONSE_HEADERS = ("content-type", "content-length", "retry-after", "location", "date")
REQUEST_REF = re.compile(r"\{requests\.([a-zA-Z_]\w*)(?:\.([\w.\[\]]+))?\}")


class RequestFailed(EngineError):
    """A request ran out of attempts (or could not run). Not retried by the node's
    transient retry — the request has its own retry policy."""

    klass = "request"

    def __init__(self, message: str, *, name: str, details: list[str] | None = None, code: str = "request_failed") -> None:
        super().__init__(message, details=details)
        self.name = name
        self.code = code


@dataclass
class AttemptTrace:
    n: int
    started_at: str
    elapsed_ms: int = 0
    request: dict[str, Any] = field(default_factory=dict)  # masked
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    body_excerpt: str | None = None  # masked, truncated
    extracted: dict[str, str] = field(default_factory=dict)  # masked when sensitive
    missing: list[str] = field(default_factory=list)
    error: str | None = None
    outcome: str = "failed"  # ok | retry | failed
    wait_s: float | None = None  # backoff before the next attempt

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, {}, [])}


@dataclass
class RequestResult:
    name: str
    kind: str
    ok: bool
    values: dict[str, Any] = field(default_factory=dict)  # Secret when sensitive
    attempts: list[AttemptTrace] = field(default_factory=list)
    error: str | None = None
    code: str | None = None
    fetched_at: float = field(default_factory=time.time)

    @property
    def status(self) -> str:
        return "ok" if self.ok else "failed"

    def trace(self) -> dict[str, Any]:
        """JSON-safe, masked — for events, the builder and the CLI."""
        return {
            "name": self.name, "request_kind": self.kind, "ok": self.ok, "error": self.error, "code": self.code,
            "values": {k: (MASK if isinstance(v, Secret) else v) for k, v in self.values.items()},
            "attempts": [a.as_dict() for a in self.attempts],
        }


@dataclass
class RequestServices:
    """What a run lends its requests. All optional, so the builder and tests can
    run requests with nothing but inputs."""

    totp: Callable[[], str] | None = None  # optional caller-supplied code callback (legacy request syntax)
    on_secret: Callable[[str], None] | None = None  # register a plaintext with the run's Redactor
    transport: httpx.AsyncBaseTransport | None = None  # tests: httpx.MockTransport
    run_started: float = field(default_factory=time.time)
    allowed_hosts: str = ""  # empty permits any host; caller supplies policy
    default_timeout_s: float = 10.0
    imap_factory: Callable[[str, int], Any] | None = None  # tests: fake IMAP client


Render = Callable[[str, bool], str]
"""render(template, reveal) — reveal=False masks Secrets (for traces)."""


class RequestRunner:
    def __init__(self, specs: dict[str, RequestSpec], services: RequestServices | None = None) -> None:
        self.specs = specs
        self.services = services or RequestServices()
        self.results: dict[str, RequestResult] = {}

    # -- state the runtime/conditions read --

    @property
    def statuses(self) -> dict[str, str]:
        return {name: (self.results[name].status if name in self.results else "pending") for name in self.specs}

    def values(self, name: str) -> dict[str, Any] | None:
        r = self.results.get(name)
        return r.values if r and r.ok else None

    def fresh_enough(self, name: str) -> bool:
        """`cache: run` reuses the first good result; `none` always refetches."""
        spec = self.specs[name]
        r = self.results.get(name)
        return spec.cache == "run" and r is not None and r.ok

    # -- the call --

    async def fetch(self, name: str, render: Render) -> RequestResult:
        if name not in self.specs:
            raise RequestFailed(f"unknown request {name!r}", name=name, code="unknown_request")
        spec = self.specs[name]
        if self.fresh_enough(name):
            return self.results[name]
        if spec.kind == "http":
            result = await self._http(name, spec, render)
        elif spec.kind == "totp":
            result = await self._totp(name, spec, render)
        else:
            result = await self._imap(name, spec, render)
        self.results[name] = result
        return result

    # -- retry loop shared by every kind --

    async def _retrying(self, name: str, spec: RequestSpec, once: Callable[[AttemptTrace], Any]) -> RequestResult:
        result = RequestResult(name=name, kind=spec.kind, ok=False)
        policy = spec.retry
        for n in range(1, policy.attempts + 1):
            attempt = AttemptTrace(n=n, started_at=datetime.now(UTC).isoformat())
            t0 = time.perf_counter()
            retryable = False
            try:
                values, retryable = await once(attempt)
            except _Retryable as exc:
                attempt.error = str(exc)
                values, retryable = None, True
            except RequestFailed:
                raise
            except Exception as exc:  # noqa: BLE001 — every failure ends up in the trace
                attempt.error = f"{type(exc).__name__}: {exc}"
                values, retryable = None, False
            attempt.elapsed_ms = int((time.perf_counter() - t0) * 1000)
            result.attempts.append(attempt)
            if values is not None:
                attempt.outcome = "ok"
                result.ok = True
                result.values = values
                result.error = None
                return result
            last = n >= policy.attempts
            if retryable and not last:
                wait = policy.backoff_s[min(n - 1, len(policy.backoff_s) - 1)] if policy.backoff_s else 0.0
                attempt.outcome, attempt.wait_s = "retry", wait
                await asyncio.sleep(wait)
                continue
            attempt.outcome = "failed"
            result.error = attempt.error or (f"missing {attempt.missing}" if attempt.missing else "failed")
            result.code = "extract_missing" if attempt.missing and not attempt.error else "request_failed"
            return result
        return result

    def _finish_values(self, spec: RequestSpec, raw: dict[str, str | None], attempt: AttemptTrace) -> dict[str, Any] | None:
        """Wrap extracted values (Secrets when sensitive), record them masked in the
        trace, and decide whether a required one is missing."""
        missing = [k for k, ex in spec.extract.items() if ex.required and raw.get(k) in (None, "")]
        out: dict[str, Any] = {}
        for k, v in raw.items():
            if v is None:
                continue
            if spec.sensitive:
                if self.services.on_secret:
                    self.services.on_secret(v)
                out[k] = Secret(v)
                attempt.extracted[k] = f"{MASK} ({len(v)} chars)"
                if attempt.body_excerpt and len(v) >= 4:
                    attempt.body_excerpt = attempt.body_excerpt.replace(v, MASK)
            else:
                out[k] = v
                attempt.extracted[k] = v[:200]
        attempt.missing = missing
        return None if missing else out

    # -- http --

    def _check_host(self, url: str) -> None:
        allowed = self.services.allowed_hosts
        if not allowed.strip():
            return
        host = (urlsplit(url).hostname or "").lower()
        for entry in (e.strip().lower() for e in allowed.split(",") if e.strip()):
            if host == entry or (entry.startswith(".") and host.endswith(entry)):
                return
        raise RequestFailed(f"host {host!r} is not in REQUESTS_ALLOWED_HOSTS", name="", code="host_not_allowed")

    async def _http(self, name: str, spec: RequestSpec, render: Render) -> RequestResult:
        url = render(spec.url or "", True)
        try:
            self._check_host(url)
        except RequestFailed as exc:
            return RequestResult(name=name, kind="http", ok=False, error=str(exc), code=exc.code)
        headers = {k: render(v, True) for k, v in spec.headers.items()}
        params = {k: render(v, True) for k, v in spec.query.items()}
        content = render(spec.body, True) if spec.body is not None else None
        json_body = _render_json(spec.json_body, lambda s: render(s, True)) if spec.json_body is not None else None
        shown = {
            "method": spec.method,
            "url": render(spec.url or "", False),
            "query": {k: render(v, False) for k, v in spec.query.items()} or None,
            "headers": {k: (MASK if _SENSITIVE_HEADER.search(k) else render(v, False)) for k, v in spec.headers.items()} or None,
            "body": _excerpt(render(spec.body, False)) if spec.body is not None else
                    (_excerpt(jsonlib.dumps(_render_json(spec.json_body, lambda s: render(s, False)))) if spec.json_body is not None else None),
        }
        shown = {k: v for k, v in shown.items() if v is not None}
        timeout = spec.timeout_s or self.services.default_timeout_s

        async def once(attempt: AttemptTrace) -> tuple[dict[str, Any] | None, bool]:
            attempt.request = shown
            async with httpx.AsyncClient(transport=self.services.transport, timeout=timeout, follow_redirects=True) as client:
                try:
                    resp = await client.request(spec.method, url, headers=headers, params=params, content=content, json=json_body)
                except httpx.HTTPError as exc:
                    attempt.error = f"{type(exc).__name__}: {exc}"
                    return None, spec.retry.on_network_error
            attempt.status = resp.status_code
            attempt.response_headers = {k: v for k, v in resp.headers.items() if k.lower() in _KEEP_RESPONSE_HEADERS}
            text = resp.text
            attempt.body_excerpt = _excerpt(self._mask(text))
            if not _status_in(resp.status_code, spec.expect_status):
                attempt.error = f"HTTP {resp.status_code} (expected {spec.expect_status})"
                return None, _status_in(resp.status_code, spec.retry.on_status)
            raw = {k: _extract(ex, text, resp.headers) for k, ex in spec.extract.items()}
            values = self._finish_values(spec, raw, attempt)
            if values is None:
                return None, spec.retry.until_extracted
            return values, False

        return await self._retrying(name, spec, once)

    # -- totp --

    async def _totp(self, name: str, spec: RequestSpec, render: Render) -> RequestResult:
        async def once(attempt: AttemptTrace) -> tuple[dict[str, Any] | None, bool]:
            if spec.secret:
                seed = render(spec.secret, True)
                attempt.request = {"source": "secret", "period": spec.period, "digits": spec.digits}
                left = seconds_left(spec.period)
                if left < _TOTP_MIN_VALIDITY_S:
                    attempt.request["waited_s"] = round(left, 2)
                    await asyncio.sleep(left + 0.05)
                code = totp(seed, digits=spec.digits, period=spec.period)
            elif self.services.totp is not None:
                attempt.request = {"source": "credential"}
                code = await asyncio.to_thread(self.services.totp)
            else:
                attempt.error = "no TOTP source: the request has no `secret` and the run's credential has no TOTP"
                return None, False
            if self.services.on_secret:
                self.services.on_secret(code)
            attempt.extracted["code"] = f"{MASK} ({len(code)} digits)"
            return {"code": Secret(code) if spec.sensitive else code}, False

        return await self._retrying(name, spec, once)

    # -- imap --

    async def _imap(self, name: str, spec: RequestSpec, render: Render) -> RequestResult:
        assert spec.imap is not None
        im = spec.imap
        host, user, password = render(im.host, True), render(im.username, True), render(im.password, True)

        async def once(attempt: AttemptTrace) -> tuple[dict[str, Any] | None, bool]:
            attempt.request = {"host": render(im.host, False), "folder": im.folder, "from": im.from_, "subject": im.subject}
            try:
                text = await asyncio.to_thread(self._imap_latest, host, im.port, user, password, im)
            except (OSError, imaplib.IMAP4.error) as exc:
                attempt.error = f"{type(exc).__name__}: {self._mask(str(exc))}"
                return None, spec.retry.on_network_error
            if text is None:
                attempt.error = "no matching message yet"
                return None, spec.retry.until_extracted
            attempt.body_excerpt = _excerpt(self._mask(text))
            raw = {k: _extract(ex, text, {}) for k, ex in spec.extract.items()}
            values = self._finish_values(spec, raw, attempt)
            if values is None:
                return None, spec.retry.until_extracted
            return values, False

        return await self._retrying(name, spec, once)

    def _imap_latest(self, host: str, port: int, user: str, password: str, im: Any) -> str | None:
        """Subject + text of the newest message that matches and arrived after the
        run started (and within ``max_age_s``)."""
        from graphwright.engine.schema import Pattern

        factory = self.services.imap_factory or (lambda h, p: imaplib.IMAP4_SSL(h, p) if p == 993 else imaplib.IMAP4(h, p))
        client = factory(host, port)
        try:
            client.login(user, password)
            client.select(im.folder, readonly=True)
            _, data = client.search(None, "ALL")
            ids = (data[0] or b"").split()[-20:]
            floor = max(self.services.run_started - 30, time.time() - im.max_age_s)
            for msg_id in reversed(ids):
                _, parts = client.fetch(msg_id, "(RFC822)")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw is None:
                    continue
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                try:
                    sent = parsedate_to_datetime(str(msg["Date"])).timestamp()
                except (TypeError, ValueError):
                    continue
                if sent < floor:
                    break  # older than anything we would accept; ids are chronological
                if im.from_ and not Pattern(im.from_).test(str(msg["From"] or "")):
                    continue
                if im.subject and not Pattern(im.subject).test(str(msg["Subject"] or "")):
                    continue
                body = msg.get_body(preferencelist=("plain", "html"))
                content = body.get_content() if body is not None else ""
                content = re.sub(r"<[^>]+>", " ", content) if body is not None and body.get_content_type() == "text/html" else content
                return f"{msg['Subject'] or ''}\n{content}"
            return None
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass

    def _mask(self, text: str) -> str:
        """Scrub values the run already knows are secret (the Redactor's job, but a
        trace may be built before the runtime redacts the event)."""
        for r in self.results.values():
            for v in r.values.values():
                if isinstance(v, Secret):
                    plain = v.reveal()
                    if plain and len(plain) >= 4:
                        text = text.replace(plain, MASK)
        return text


class _Retryable(Exception):
    pass


# --- helpers -------------------------------------------------------------------------------


def _status_in(code: int, allowed: list[int | str]) -> bool:
    for a in allowed:
        if isinstance(a, int) and code == a:
            return True
        if isinstance(a, str):
            a = a.strip().lower()
            if a.endswith("xx") and a[0].isdigit() and code // 100 == int(a[0]):
                return True
            if a.isdigit() and code == int(a):
                return True
    return False


_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def json_path(data: Any, path: str) -> Any:
    """`data.items[0].code` (leading `$.` optional) → value, or None."""
    path = path.removeprefix("$").removeprefix(".")
    value = data
    for key, index in _PATH_TOKEN.findall(path):
        if index:
            if not isinstance(value, list) or int(index) >= len(value):
                return None
            value = value[int(index)]
        else:
            if not isinstance(value, dict) or key not in value:
                return None
            value = value[key]
    return value


def _extract(ex: Extractor, text: str, headers: Any) -> str | None:
    if ex.header is not None:
        v = headers.get(ex.header) if hasattr(headers, "get") else None
        return str(v) if v is not None else None
    if ex.regex is not None:
        m = re.search(ex.regex, text)
        if not m:
            return None
        return m.group(1) if m.groups() else m.group(0)
    try:
        data = jsonlib.loads(text)
    except ValueError:
        return None
    v = json_path(data, ex.json_path or "")
    if v is None:
        return None
    return v if isinstance(v, str) else jsonlib.dumps(v) if isinstance(v, (dict, list)) else str(v)


def _render_json(value: Any, render: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return render(value)
    if isinstance(value, dict):
        return {k: _render_json(v, render) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_json(v, render) for v in value]
    return value


def _excerpt(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= _BODY_EXCERPT else text[:_BODY_EXCERPT] + f"… ({len(text)} chars)"


def template_strings(spec: RequestSpec) -> list[str]:
    """Every template string a request renders (url, headers, query, body, json, secret, imap)."""
    out = [x for x in (spec.url, spec.body, spec.secret) if x]
    out += list(spec.headers.values()) + list(spec.query.values())
    if spec.imap is not None:
        out += [spec.imap.host, spec.imap.username, spec.imap.password]

    def walk(v: Any) -> None:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(spec.json_body)
    return out


def dependencies(spec: RequestSpec) -> list[str]:
    """Other requests this one reads values from, in first-use order."""
    return list(dict.fromkeys(r for text in template_strings(spec) for r, _ in references(text)))


def references(text: str) -> list[tuple[str, str | None]]:
    """`{requests.otp.code}` occurrences in a template → [(request, field)]."""
    return [(m.group(1), m.group(2)) for m in REQUEST_REF.finditer(text or "")]
