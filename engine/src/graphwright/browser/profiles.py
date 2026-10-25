"""Coherent machine profiles for BrowserContext.

Each profile is a static, internally-coherent bundle of browser identity fields:
UA, viewport, locale, timezone, color-scheme, sec-ch-ua, accept-language, etc.

Do NOT randomize fields at runtime — random combinations produce incoherent
profiles (e.g. en-US locale + Asia/Tokyo timezone) which is itself a detection
signal. Define new profiles by adding entries to MACHINE_PROFILES below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int


@dataclass(frozen=True)
class MachineProfile:
    name: str
    user_agent: str
    chrome_major_version: int
    viewport: Viewport
    locale: str
    timezone_id: str
    color_scheme: str
    reduced_motion: str
    forced_colors: str
    has_touch: bool
    permissions: list[str]
    sec_ch_ua: str
    sec_ch_ua_platform: str
    accept_language: str
    webgl_vendor: str
    webgl_renderer: str

    def context_kwargs(self) -> dict[str, Any]:
        """Subset suitable for `browser.new_context(**profile.context_kwargs())`."""
        return {
            "user_agent": self.user_agent,
            "viewport": {"width": self.viewport.width, "height": self.viewport.height},
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "color_scheme": self.color_scheme,
            "reduced_motion": self.reduced_motion,
            "forced_colors": self.forced_colors,
            "has_touch": self.has_touch,
            "permissions": list(self.permissions),
        }

    def extra_http_headers(self) -> dict[str, str]:
        return {
            "Accept-Language": self.accept_language,
            "Accept-Encoding": "gzip, deflate, br",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "Upgrade-Insecure-Requests": "1",
        }


MACHINE_PROFILES: dict[str, MachineProfile] = {
    "win11_chrome_1920": MachineProfile(
        name="win11_chrome_1920",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        chrome_major_version=124,
        viewport=Viewport(width=1920, height=1080),
        locale="en-US",
        timezone_id="America/New_York",
        color_scheme="light",
        reduced_motion="no-preference",
        forced_colors="none",
        has_touch=False,
        permissions=["notifications", "geolocation"],
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
        accept_language="en-US,en;q=0.9",
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer="ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    ),
    "mac_chrome_1440": MachineProfile(
        name="mac_chrome_1440",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        chrome_major_version=124,
        viewport=Viewport(width=1440, height=900),
        locale="en-US",
        timezone_id="America/Los_Angeles",
        color_scheme="light",
        reduced_motion="no-preference",
        forced_colors="none",
        has_touch=False,
        permissions=["notifications", "geolocation"],
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"macOS"',
        accept_language="en-US,en;q=0.9",
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)",
    ),
}


def get_profile(name: str) -> MachineProfile:
    if name not in MACHINE_PROFILES:
        raise KeyError(
            f"Machine profile {name!r} not registered. Available: {sorted(MACHINE_PROFILES)}"
        )
    return MACHINE_PROFILES[name]
