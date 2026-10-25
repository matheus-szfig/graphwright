"""Coherence assertions for MACHINE_PROFILES — these are signals detection systems
cross-reference. The profiles must be internally consistent."""

from __future__ import annotations

import re

import pytest

from graphwright.browser.profiles import MACHINE_PROFILES, get_profile


@pytest.mark.parametrize("name", list(MACHINE_PROFILES))
def test_sec_ch_ua_version_matches_user_agent(name: str) -> None:
    profile = MACHINE_PROFILES[name]
    ua_match = re.search(r"Chrome/(\d+)", profile.user_agent)
    assert ua_match, f"profile {name}: UA has no Chrome version"
    ua_chrome_version = int(ua_match.group(1))

    # Skip the GREASE brand "Not-A.Brand" — its version is intentionally distinct
    # from the real Chrome version. Only Chromium + Google Chrome must match the UA.
    branded = re.findall(r'"([^"]+)";v="(\d+)"', profile.sec_ch_ua)
    real_brands = {brand: int(version) for brand, version in branded if "Not" not in brand}
    assert real_brands, f"profile {name}: sec-ch-ua has no non-GREASE brands"
    assert all(v == ua_chrome_version for v in real_brands.values()), (
        f"profile {name}: sec-ch-ua brands {real_brands} don't all match UA Chrome {ua_chrome_version}"
    )
    assert profile.chrome_major_version == ua_chrome_version


@pytest.mark.parametrize("name", list(MACHINE_PROFILES))
def test_accept_language_matches_locale(name: str) -> None:
    profile = MACHINE_PROFILES[name]
    assert profile.accept_language.startswith(profile.locale), (
        f"profile {name}: Accept-Language {profile.accept_language!r} must start with locale {profile.locale!r}"
    )


@pytest.mark.parametrize("name", list(MACHINE_PROFILES))
def test_sec_ch_ua_platform_matches_user_agent_os(name: str) -> None:
    profile = MACHINE_PROFILES[name]
    ua = profile.user_agent
    platform = profile.sec_ch_ua_platform
    if "Windows" in ua:
        assert "Windows" in platform
    elif "Macintosh" in ua or "Mac OS X" in ua:
        assert "macOS" in platform
    else:
        pytest.fail(f"profile {name}: unrecognized OS in UA: {ua!r}")


def test_get_profile_raises_keyerror_for_unknown() -> None:
    with pytest.raises(KeyError, match="not registered"):
        get_profile("nonexistent_profile")


def test_context_kwargs_includes_required_fields() -> None:
    profile = MACHINE_PROFILES["win11_chrome_1920"]
    kwargs = profile.context_kwargs()
    assert kwargs["user_agent"] == profile.user_agent
    assert kwargs["viewport"] == {"width": 1920, "height": 1080}
    assert kwargs["locale"] == "en-US"
    assert kwargs["timezone_id"] == "America/New_York"


def test_extra_http_headers_match_profile() -> None:
    profile = MACHINE_PROFILES["win11_chrome_1920"]
    headers = profile.extra_http_headers()
    assert headers["Accept-Language"] == profile.accept_language
    assert headers["sec-ch-ua"] == profile.sec_ch_ua
    assert headers["sec-ch-ua-platform"] == profile.sec_ch_ua_platform
