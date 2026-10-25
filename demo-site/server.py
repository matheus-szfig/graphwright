"""Acme Records — a fictional records registry the example graphs run against.

Standard library only, so it runs anywhere Python does:

    python demo-site/server.py --port 8090          # http://127.0.0.1:8090
    docker compose up demo-site                     # http://demo-site:8090 on the compose network

Tests start it in-process on an ephemeral port::

    from server import serve
    server, base_url = serve(port=0)   # background thread
    ...
    server.shutdown()

Pages (server-rendered HTML, no JavaScript):

    /                     home
    /search               public search: "Reference number" (e.g. ACM-2024-000123),
                          "Record type" select, "Search" button. GET with ?ref=&type=
                          redirects to the record, or re-renders with a role=alert
                          "No record found …" message.
    /records/<id>         record detail: <h1>, a definition list (#record-title,
                          #record-status, #record-owner) and an "Events" table
                          (Date · Event · By) with a <thead>.
    /login                sign-in step 1: Username + Password → /login/verify,
                          or "Invalid username or password" (role=alert).
    /login/verify         step 2: 6-digit "Verification code" (RFC 6238 TOTP, SHA-1,
                          30 s, ±1 window) → /account, or "Invalid verification code".
    /account              dashboard (signed-in only): "Welcome, Demo User" and a
                          "Recent activity" table. POST /logout signs out.

OTP relay API — what an SMS/e-mail relay looks like to a graph's http request.
Every call needs ``Authorization: Bearer demo-relay-token``:

    POST /api/otp/request   {"account": "demo"}  → {"data": {"requested": true}}
    GET  /api/otp/latest?account=demo            → {"data": {"code": null}} for the
                          first two polls after a request, then {"data": {"code": "123456"}}
                          (the current TOTP) — so `until_extracted` polling is exercised.

Demo credentials (obviously fake, fixed): username ``demo``, password
``demo-password``, TOTP seed ``JBSWY3DPEHPK3PXP``. State is in memory and per
process; restarting forgets sessions and relay counters.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import secrets
import struct
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

DEMO_USER = "demo"
DEMO_PASSWORD = "demo-password"
DEMO_TOTP_SEED = "JBSWY3DPEHPK3PXP"
DEMO_RELAY_TOKEN = "demo-relay-token"
RELAY_EMPTY_POLLS = 2
COOKIE = "acme_session"

RECORDS: dict[str, dict[str, Any]] = {
    "ACM-2024-000123": {
        "id": 48213, "type": "Permit", "title": "Warehouse expansion permit", "status": "Active",
        "owner": "Jane Doe",
        "events": [
            ("2024-03-02", "Application received", "Front desk"),
            ("2024-03-18", "Site inspection passed", "Inspector R. Lee"),
            ("2024-04-01", "Permit issued", "Licensing office"),
        ],
    },
    "ACM-2023-004567": {
        "id": 39120, "type": "License", "title": "Food service license", "status": "Expired",
        "owner": "Northside Bistro LLC",
        "events": [
            ("2023-01-10", "License issued", "Licensing office"),
            ("2024-01-10", "License expired", "System"),
        ],
    },
    "ACM-2025-000042": {
        "id": 51007, "type": "Certificate", "title": "Occupancy certificate", "status": "Pending review",
        "owner": "Acme Holdings",
        "events": [("2025-06-30", "Request submitted", "Online portal")],
    },
}
RECORD_TYPES = ["Any type", "Permit", "License", "Certificate"]
BY_ID = {r["id"]: (ref, r) for ref, r in RECORDS.items()}


# --- TOTP (RFC 6238, the same algorithm the engine uses) ------------------------------


def totp(secret: str, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def totp_ok(code: str) -> bool:
    now = time.time()
    return any(hmac.compare_digest(code, totp(DEMO_TOTP_SEED, now + d * 30)) for d in (-1, 0, 1))


# --- state -----------------------------------------------------------------------------


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.relay_polls: dict[str, int] = {}


# --- HTML ------------------------------------------------------------------------------

CSS = """
body { margin: 0; font-family: system-ui, sans-serif; color: #1c2430; background: #f6f7f9; }
header { background: #1f3a5f; color: #fff; padding: 12px 24px; display: flex; gap: 24px; align-items: center; }
header a { color: #fff; text-decoration: none; }
header .brand { font-weight: 700; font-size: 18px; }
main { max-width: 760px; margin: 24px auto; background: #fff; padding: 24px 32px; border-radius: 8px; }
label { display: block; margin-top: 12px; font-weight: 600; }
input, select { font-size: 15px; padding: 6px 8px; margin-top: 4px; width: 280px; }
button { margin-top: 16px; font-size: 15px; padding: 8px 18px; background: #1f3a5f; color: #fff; border: 0; border-radius: 4px; }
.alert { margin: 12px 0; padding: 10px 12px; background: #fdecea; color: #8a1c1c; border-radius: 4px; }
dl { display: grid; grid-template-columns: 140px 1fr; gap: 6px 12px; }
dt { font-weight: 600; }
table { border-collapse: collapse; margin-top: 12px; width: 100%; }
th, td { border-bottom: 1px solid #dde1e6; padding: 6px 8px; text-align: left; }
footer { text-align: center; color: #6b7480; font-size: 13px; margin: 24px; }
"""


def page(title: str, body: str, *, signed_in: bool = False) -> str:
    account = '<a href="/account">My account</a>' if signed_in else '<a href="/login">Sign in</a>'
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{html.escape(title)} · Acme Records</title><style>{CSS}</style></head>
<body>
<header>
  <a class="brand" href="/">Acme Records</a>
  <nav aria-label="Main"><a href="/search">Search records</a> &nbsp; {account}</nav>
</header>
<main>
{body}
</main>
<footer>Acme Records is a fictional registry used to demonstrate graphwright.</footer>
</body>
</html>"""


def alert(text: str | None) -> str:
    return f'<div class="alert" role="alert">{html.escape(text)}</div>' if text else ""


def search_page(ref: str = "", rtype: str = "Any type", message: str | None = None) -> str:
    options = "".join(
        f'<option{" selected" if t == rtype else ""}>{html.escape(t)}</option>' for t in RECORD_TYPES
    )
    return page("Search records", f"""
<h1>Search records</h1>
{alert(message)}
<form id="search" action="/search" method="get">
  <label for="ref">Reference number</label>
  <input id="ref" name="ref" type="text" placeholder="ACM-2024-000123" value="{html.escape(ref)}" required>
  <label for="type">Record type</label>
  <select id="type" name="type">{options}</select>
  <div><button type="submit">Search</button></div>
</form>""")


def record_page(ref: str, rec: dict[str, Any], *, signed_in: bool) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(d)}</td><td>{html.escape(e)}</td><td>{html.escape(b)}</td></tr>"
        for d, e, b in rec["events"]
    )
    return page(ref, f"""
<h1>Record {html.escape(ref)}</h1>
<dl>
  <dt>Title</dt><dd id="record-title">{html.escape(rec["title"])}</dd>
  <dt>Type</dt><dd id="record-type">{html.escape(rec["type"])}</dd>
  <dt>Status</dt><dd id="record-status">{html.escape(rec["status"])}</dd>
  <dt>Owner</dt><dd id="record-owner">{html.escape(rec["owner"])}</dd>
</dl>
<table id="events">
  <caption>Events</caption>
  <thead><tr><th>Date</th><th>Event</th><th>By</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<p><a href="/search">New search</a></p>""", signed_in=signed_in)


def login_page(message: str | None = None, username: str = "") -> str:
    return page("Sign in", f"""
<h1>Sign in</h1>
{alert(message)}
<form id="login" action="/login" method="post">
  <label for="username">Username</label>
  <input id="username" name="username" type="text" autocomplete="username" value="{html.escape(username)}" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <div><button type="submit">Sign in</button></div>
</form>""")


def verify_page(message: str | None = None) -> str:
    return page("Two-step verification", f"""
<h1>Two-step verification</h1>
<p>Enter the 6-digit code from your authenticator app.</p>
{alert(message)}
<form id="verify" action="/login/verify" method="post">
  <label for="code">Verification code</label>
  <input id="code" name="code" type="text" inputmode="numeric" maxlength="6" autocomplete="one-time-code" required>
  <div><button type="submit">Verify</button></div>
</form>""")


def account_page() -> str:
    return page("My account", """
<h1>Welcome, Demo User</h1>
<p>You are signed in as <strong id="account-user">demo</strong>.</p>
<table id="activity">
  <caption>Recent activity</caption>
  <thead><tr><th>Date</th><th>Activity</th></tr></thead>
  <tbody>
    <tr><td>2025-07-01</td><td>Renewed license ACM-2023-004567</td></tr>
    <tr><td>2025-06-30</td><td>Submitted ACM-2025-000042</td></tr>
  </tbody>
</table>
<form action="/logout" method="post"><button type="submit">Sign out</button></form>""", signed_in=True)


# --- handler -------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "AcmeRecords/1.0"
    state: State  # set by serve()

    def log_message(self, fmt: str, *args: Any) -> None:  # keep test output quiet
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # -- plumbing --

    def _session(self) -> tuple[str, dict[str, Any]]:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        sid = cookie[COOKIE].value if COOKIE in cookie else ""
        with self.state.lock:
            if sid not in self.state.sessions:
                sid = secrets.token_urlsafe(16)
                self.state.sessions[sid] = {}
            return sid, self.state.sessions[sid]

    def _send(self, status: int, body: str, *, sid: str | None = None, ctype: str = "text/html; charset=utf-8",
              headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if sid:
            self.send_header("Set-Cookie", f"{COOKIE}={sid}; Path=/; HttpOnly; SameSite=Lax")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, location: str, sid: str | None = None) -> None:
        self._send(HTTPStatus.SEE_OTHER, "", sid=sid, headers={"Location": location})

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload), ctype="application/json")

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if "json" in (self.headers.get("Content-Type") or ""):
            try:
                data = json.loads(raw or "{}")
                return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
            except ValueError:
                return {}
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _relay_authorized(self) -> bool:
        return hmac.compare_digest(self.headers.get("Authorization") or "", f"Bearer {DEMO_RELAY_TOKEN}")

    # -- routes --

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path.rstrip("/") or "/"
        sid, sess = self._session()
        signed_in = bool(sess.get("user"))

        if path == "/":
            return self._send(200, page("Home", """
<h1>Acme Records</h1>
<p>Look up permits, licenses and certificates by reference number.</p>
<p><a href="/search">Search records</a></p>""", signed_in=signed_in), sid=sid)
        if path == "/healthz":
            return self._json(200, {"status": "ok"})
        if path == "/search":
            ref = query.get("ref", "").strip()
            rtype = query.get("type", "Any type")
            if not ref:
                return self._send(200, search_page(), sid=sid)
            rec = RECORDS.get(ref.upper())
            if rec is None or (rtype not in ("", "Any type") and rec["type"] != rtype):
                return self._send(200, search_page(ref, rtype, f"No record found for {ref}."), sid=sid)
            return self._redirect(f"/records/{rec['id']}", sid)
        if path.startswith("/records/"):
            try:
                ref, rec = BY_ID[int(path.rsplit("/", 1)[1])]
            except (KeyError, ValueError):
                return self._send(404, page("Not found", "<h1>Not found</h1>"), sid=sid)
            return self._send(200, record_page(ref, rec, signed_in=signed_in), sid=sid)
        if path == "/login":
            return self._send(200, login_page(), sid=sid)
        if path == "/login/verify":
            if not sess.get("pending"):
                return self._redirect("/login", sid)
            return self._send(200, verify_page(), sid=sid)
        if path == "/account":
            if not signed_in:
                return self._redirect("/login", sid)
            return self._send(200, account_page(), sid=sid)
        if path == "/api/otp/latest":
            if not self._relay_authorized():
                return self._json(401, {"detail": "unauthorized"})
            account = query.get("account", "")
            if account != DEMO_USER:
                return self._json(404, {"detail": "unknown account"})
            with self.state.lock:
                polls = self.state.relay_polls.get(account, 0) + 1
                self.state.relay_polls[account] = polls
            code = totp(DEMO_TOTP_SEED) if polls > RELAY_EMPTY_POLLS else None
            return self._json(200, {"data": {"account": account, "code": code}})
        return self._send(404, page("Not found", "<h1>Not found</h1>"), sid=sid)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/") or "/"
        form = self._form()
        sid, sess = self._session()

        if path == "/login":
            if form.get("username") == DEMO_USER and hmac.compare_digest(form.get("password", ""), DEMO_PASSWORD):
                sess.clear()
                sess["pending"] = DEMO_USER
                return self._redirect("/login/verify", sid)
            return self._send(200, login_page("Invalid username or password.", form.get("username", "")), sid=sid)
        if path == "/login/verify":
            if not sess.get("pending"):
                return self._redirect("/login", sid)
            if totp_ok(form.get("code", "").strip()):
                sess["user"] = sess.pop("pending")
                return self._redirect("/account", sid)
            return self._send(200, verify_page("Invalid verification code."), sid=sid)
        if path == "/logout":
            sess.clear()
            return self._redirect("/", sid)
        if path == "/api/otp/request":
            if not self._relay_authorized():
                return self._json(401, {"detail": "unauthorized"})
            account = form.get("account", "")
            if account != DEMO_USER:
                return self._json(404, {"detail": "unknown account"})
            with self.state.lock:
                self.state.relay_polls[account] = 0
            return self._json(200, {"data": {"requested": True}})
        return self._send(404, page("Not found", "<h1>Not found</h1>"), sid=sid)


def serve(host: str = "127.0.0.1", port: int = 0, *, verbose: bool = False) -> tuple[ThreadingHTTPServer, str]:
    """Start the site in a daemon thread. Returns (server, base_url); call
    ``server.shutdown()`` to stop it."""
    handler = type("BoundHandler", (Handler,), {"state": State()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.verbose = verbose  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, name="acme-records", daemon=True)
    thread.start()
    bound_host, bound_port = server.server_address[:2]
    shown = "127.0.0.1" if bound_host in ("0.0.0.0", "") else bound_host
    return server, f"http://{shown}:{bound_port}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Acme Records demo site")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()
    handler = type("BoundHandler", (Handler,), {"state": State()})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.verbose = True  # type: ignore[attr-defined]
    print(f"Acme Records on http://{args.host}:{args.port}  (user {DEMO_USER} / {DEMO_PASSWORD}, TOTP seed {DEMO_TOTP_SEED})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
