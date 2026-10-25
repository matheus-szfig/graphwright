from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import fakeredis
import pytest

from graphwright.storage import cache

ENGINE = Path(__file__).resolve().parents[1]
DEMO_SITE = ENGINE.parent / "demo-site" / "server.py"
GRAPHS = ENGINE / "graphs"
FIXTURES = Path(__file__).with_name("fixtures")
GOLDENS = FIXTURES / "goldens"

BROWSER_TESTS = os.environ.get("GW_BROWSER_TESTS") == "1"
browser_only = pytest.mark.skipif(not BROWSER_TESTS, reason="browser tests are opt-in (GW_BROWSER_TESTS=1)")


def load_demo_site():
    """Import demo-site/server.py as a module (it is not a package)."""
    if "acme_demo_site" in sys.modules:
        return sys.modules["acme_demo_site"]
    spec = importlib.util.spec_from_file_location("acme_demo_site", DEMO_SITE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["acme_demo_site"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache, "_client", client)
    yield client
    cache.reset_client()


@pytest.fixture(scope="session")
def demo_site():
    """(module, base_url) of an in-process Acme Records server."""
    site = load_demo_site()
    server, base_url = site.serve(port=0)
    yield site, base_url
    server.shutdown()
