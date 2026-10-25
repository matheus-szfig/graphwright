"""Per-worker Browser lifecycle + per-task context provisioning.

One BrowserPool exists per worker process — instantiated by workers/hooks.py at
worker_process_init, torn down at worker_process_shutdown. Tasks borrow a
context for a target domain via the `context_for(domain)` async context manager;
session storage is loaded on acquire and persisted on release.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from playwright_stealth import Stealth

from graphwright.browser.profiles import MachineProfile, get_profile
from graphwright.config import HeadlessMode, settings
from graphwright.storage import blobs

log = logging.getLogger(__name__)


_STEALTH_JS_PATH = Path(__file__).parent / "init_scripts" / "stealth.js"


# Known third-party detection/anti-bot vendor domains. Routed to abort when
# settings.browser_telemetry_block_enabled is True (mitigation #8, optional).
DETECTION_VENDOR_DOMAINS: tuple[str, ...] = (
    "geo.captcha-delivery.com",
    "api.perimeterx.net",
    "challenges.cloudflare.com",
    "client.perimeterx.net",
    "captcha.com",
)


_LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=IsolateOrigins,site-per-process",
)

_IGNORED_DEFAULT_ARGS: tuple[str, ...] = ("--enable-automation",)


def _resolve_headless(value: HeadlessMode) -> bool:
    # Playwright's Python launch() accepts a bool only for `headless`.
    # Modern Chromium (since Playwright 1.47) uses the new headless mode
    # automatically when headless=True — chrome-headless-shell is no longer
    # the default — so "new" and "true" map to the same launch path.
    return value != "false"


def _build_stealth_config_script(profile: MachineProfile, seed: int) -> str:
    payload = {
        "seed": seed,
        "webglVendor": profile.webgl_vendor,
        "webglRenderer": profile.webgl_renderer,
    }
    return f"window.__rpaStealthConfig = {json.dumps(payload)};"


class BrowserPool:
    """One Playwright Browser instance per worker process.

    Contexts are created per task (per target domain) and disposed on release,
    which is when session_state is persisted to blob storage.
    """

    def __init__(
        self,
        profile_name: str,
        *,
        headless: HeadlessMode = "true",
    ) -> None:
        self.profile: MachineProfile = get_profile(profile_name)
        self.headless: HeadlessMode = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._stealth: Stealth | None = None
        self._stealth_js: str = _STEALTH_JS_PATH.read_text(encoding="utf-8")

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=_resolve_headless(self.headless),
            args=list(_LAUNCH_ARGS),
            ignore_default_args=list(_IGNORED_DEFAULT_ARGS),
        )
        self._stealth = Stealth(
            navigator_user_agent_override=self.profile.user_agent,
            sec_ch_ua_override=self.profile.sec_ch_ua,
            webgl_vendor_override=self.profile.webgl_vendor,
            webgl_renderer_override=self.profile.webgl_renderer,
            navigator_languages_override=(self.profile.locale, self.profile.locale.split("-")[0]),
            navigator_platform_override=(
                "Win32" if "Windows" in self.profile.sec_ch_ua_platform else "MacIntel"
            ),
        )
        log.info(
            "BrowserPool started profile=%s headless=%s",
            self.profile.name,
            self.headless,
        )

    async def shutdown(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._stealth = None

    @asynccontextmanager
    async def context_for(
        self,
        domain: str,
        *,
        persist_state: bool = True,
        client_certificates: list[dict] | None = None,
        authenticated: bool = False,
    ) -> AsyncIterator[BrowserContext]:
        """``persist_state=False`` neither loads nor stores the per-domain cookie
        jar — required for authenticated runs, where the jar would otherwise be
        one user's session handed to the next run on the same site.
        ``client_certificates`` is Playwright's option (TLS client certificates).
        ``authenticated`` marks a run that carries a credential: it refuses to start
        while ``BROWSER_IGNORE_HTTPS_ERRORS`` is on."""
        context = await self._acquire(
            domain, persist_state=persist_state, client_certificates=client_certificates,
            authenticated=authenticated,
        )
        try:
            yield context
        finally:
            await self._release(context, domain if persist_state else "")

    async def _acquire(
        self,
        domain: str,
        *,
        persist_state: bool = True,
        client_certificates: list[dict] | None = None,
        authenticated: bool = False,
    ) -> BrowserContext:
        assert self._browser is not None and self._stealth is not None, "BrowserPool.start() not called"

        storage_state = await blobs.get_session_state(domain) if domain and persist_state else None
        if storage_state is not None:
            log.info("loaded existing session_state for domain=%s", domain)

        context_kwargs = self.profile.context_kwargs()
        if storage_state is not None:
            context_kwargs["storage_state"] = storage_state
        if client_certificates:
            context_kwargs["client_certificates"] = client_certificates
        if settings.browser_ignore_https_errors:
            if client_certificates or authenticated:
                # An authenticated run over a connection anyone can impersonate
                # would hand the credential to the impersonator.
                raise RuntimeError("BROWSER_IGNORE_HTTPS_ERRORS is on; refusing an authenticated run")
            context_kwargs["ignore_https_errors"] = True

        context = await self._browser.new_context(**context_kwargs)

        await self._stealth.apply_stealth_async(context)

        seed = random.randint(1, 7)
        await context.add_init_script(_build_stealth_config_script(self.profile, seed))
        await context.add_init_script(self._stealth_js)

        await context.set_extra_http_headers(self.profile.extra_http_headers())

        if settings.browser_telemetry_block_enabled:
            await context.route("**/*", _block_telemetry)

        return context

    async def _release(self, context: BrowserContext, domain: str) -> None:
        try:
            if domain:
                state = await context.storage_state()
                await blobs.put_session_state(domain, state)
                log.info("persisted session_state for domain=%s", domain)
        except Exception:
            log.exception("failed to persist session_state for domain=%s", domain)
        finally:
            await context.close()


async def _block_telemetry(route, request) -> None:
    if any(d in request.url for d in DETECTION_VENDOR_DOMAINS):
        await route.abort()
    else:
        await route.continue_()
