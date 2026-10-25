"""BrowserPool unit tests — Playwright + stealth + blobs all mocked.

We assert the *sequence* of operations applied to a new context, since detection
systems care about coherence: profile → stealth → init script → headers → optional
telemetry route. Also assert _release runs even when the body of `context_for`
raises (session-state must persist on failure too).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from graphwright.browser.pool import BrowserPool


@pytest.fixture
def fake_browser_components(monkeypatch):
    """Patch async_playwright().start() + Stealth + blob session helpers."""
    browser = MagicMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=MagicMock())
    context.add_init_script = AsyncMock()
    context.set_extra_http_headers = AsyncMock()
    context.route = AsyncMock()
    context.storage_state = AsyncMock(return_value={"cookies": []})
    context.close = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    playwright = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    playwright.stop = AsyncMock()

    pw_starter = MagicMock()
    pw_starter.start = AsyncMock(return_value=playwright)
    monkeypatch.setattr("graphwright.browser.pool.async_playwright", lambda: pw_starter)

    stealth_instance = MagicMock()
    stealth_instance.apply_stealth_async = AsyncMock()
    monkeypatch.setattr("graphwright.browser.pool.Stealth", lambda **_: stealth_instance)

    monkeypatch.setattr(
        "graphwright.browser.pool.blobs.get_session_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "graphwright.browser.pool.blobs.put_session_state",
        AsyncMock(),
    )
    return {
        "playwright": playwright,
        "browser": browser,
        "context": context,
        "stealth": stealth_instance,
    }


async def test_pool_applies_full_mitigation_stack(fake_browser_components, monkeypatch):
    monkeypatch.setattr("graphwright.browser.pool.settings.browser_telemetry_block_enabled", False)
    pool = BrowserPool("win11_chrome_1920", headless="true")
    await pool.start()

    async with pool.context_for("example.com") as ctx:
        assert ctx is fake_browser_components["context"]

    ctx = fake_browser_components["context"]
    fake_browser_components["stealth"].apply_stealth_async.assert_awaited_once_with(ctx)
    # one config script + one main stealth.js script
    assert ctx.add_init_script.await_count == 2
    ctx.set_extra_http_headers.assert_awaited_once()
    # no telemetry route when flag off
    ctx.route.assert_not_called()
    ctx.storage_state.assert_awaited_once()
    ctx.close.assert_awaited_once()


async def test_pool_registers_telemetry_route_when_flag_enabled(fake_browser_components, monkeypatch):
    monkeypatch.setattr("graphwright.browser.pool.settings.browser_telemetry_block_enabled", True)
    pool = BrowserPool("win11_chrome_1920", headless="true")
    await pool.start()

    async with pool.context_for("example.com"):
        pass

    fake_browser_components["context"].route.assert_awaited_once()


async def test_pool_passes_loaded_session_state_to_new_context(fake_browser_components, monkeypatch):
    stored_state = {"cookies": [{"name": "returning_user", "value": "1"}]}
    monkeypatch.setattr(
        "graphwright.browser.pool.blobs.get_session_state",
        AsyncMock(return_value=stored_state),
    )

    pool = BrowserPool("win11_chrome_1920", headless="true")
    await pool.start()

    async with pool.context_for("example.com"):
        pass

    new_context_kwargs = fake_browser_components["browser"].new_context.call_args.kwargs
    assert new_context_kwargs.get("storage_state") == stored_state


async def test_pool_persists_session_state_even_on_exception(fake_browser_components, monkeypatch):
    put_mock = AsyncMock()
    monkeypatch.setattr("graphwright.browser.pool.blobs.put_session_state", put_mock)

    pool = BrowserPool("win11_chrome_1920", headless="true")
    await pool.start()

    with pytest.raises(RuntimeError, match="boom"):
        async with pool.context_for("example.com"):
            raise RuntimeError("boom")

    put_mock.assert_awaited_once()
    args, _ = put_mock.await_args
    assert args[0] == "example.com"


@pytest.mark.parametrize(
    "headless_setting,expected_launch_arg",
    [("true", True), ("false", False), ("new", True)],
)
async def test_pool_respects_headless_setting(
    fake_browser_components, headless_setting, expected_launch_arg
):
    pool = BrowserPool("win11_chrome_1920", headless=headless_setting)
    await pool.start()

    launch_kwargs = fake_browser_components["playwright"].chromium.launch.call_args.kwargs
    assert launch_kwargs["headless"] == expected_launch_arg
    assert "--disable-blink-features=AutomationControlled" in launch_kwargs["args"]
    assert launch_kwargs["ignore_default_args"] == ["--enable-automation"]
