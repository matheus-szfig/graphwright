"""Regenerate the PageState goldens in tests/fixtures/goldens/.

    uv run python tests/capture_goldens.py            # needs Chromium (uv run playwright install chromium)

Goldens are what the pure-Python tests (compile, recognize, resolve, transitions)
run against, so they must come from the real observer on real pages. Here the
pages are the Acme Records demo site (started in-process) and the legacy-portal
fixture (served through request interception, with its iframe). The demo site's
ephemeral address is rewritten to its compose address, http://demo-site:8090, so
the files are stable across captures.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from conftest import FIXTURES, GOLDENS, load_demo_site  # noqa: E402

from graphwright.observe.observer import Observer  # noqa: E402

STABLE_BASE = "http://demo-site:8090"


async def capture() -> list[Path]:
    from playwright.async_api import async_playwright

    site = load_demo_site()
    server, base = site.serve(port=0)
    host = base.split("://", 1)[1].split(":", 1)[0]
    GOLDENS.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-features=IsolateOrigins,site-per-process"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-US")
        page = await context.new_page()

        async def snap(name: str) -> None:
            await page.wait_for_load_state("domcontentloaded")
            obs = Observer(page)
            state = await obs.observe()
            await obs.close()
            # observed_at and signature change on every capture (the signature hashes the
            # ephemeral URL), and so do timings; drop them so a re-capture of an unchanged page is a no-op diff.
            data = state.model_dump(mode="json", exclude={"observed_at", "signature"})
            data["stats"].update(extract_ms=0.0, total_ms=0.0)  # timings vary run to run
            text = json.dumps(data, ensure_ascii=False, indent=1)
            text = text.replace(base, STABLE_BASE).replace(f'"portal": "{host}"', '"portal": "demo-site"')
            path = GOLDENS / f"{name}.json"
            path.write_text(text + "\n", encoding="utf-8")
            written.append(path)

        await page.goto(f"{base}/search")
        await snap("acme_search_form")
        await page.goto(f"{base}/search?ref=ACM-1999-000001&type=Any+type")
        await snap("acme_not_found")
        await page.goto(f"{base}/search?ref=ACM-2024-000123&type=Any+type")
        await snap("acme_record_detail")
        await page.goto(f"{base}/login")
        await snap("acme_login")
        await page.fill("#username", site.DEMO_USER)
        await page.fill("#password", site.DEMO_PASSWORD)
        await page.click("button[type=submit]")
        await page.wait_for_url("**/login/verify")
        await snap("acme_verify")
        await page.fill("#code", site.totp(site.DEMO_TOTP_SEED))
        await page.click("button[type=submit]")
        await page.wait_for_url("**/account")
        await snap("acme_account")

        async def serve_fixture(route, request) -> None:
            path = request.url.split("portal.test", 1)[1].split("?")[0]
            name = "legacy_portal_child.html" if path == "/child.html" else "legacy_portal.html"
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=(FIXTURES / name).read_text(encoding="utf-8"))

        await page.route("https://portal.test/**", serve_fixture)
        await page.goto("https://portal.test/cases/open.do?conversationId=xyz")
        await page.wait_for_timeout(300)  # let the iframe load
        await snap("legacy_portal")
        await browser.close()

    server.shutdown()
    return written


def main() -> int:
    for path in asyncio.run(capture()):
        print(f"wrote {path.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
