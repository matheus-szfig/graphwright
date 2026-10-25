"""Credential providers (file, env, http lease), TOTP, client certificates, and how
a credential becomes graph inputs (catalog.build_inputs / validate_inputs)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from conftest import GRAPHS

from graphwright import credentials
from graphwright.catalog import (
    GraphInvalid,
    GraphNotFound,
    InputsInvalid,
    build_inputs,
    get_graph,
    list_graphs,
    load_graph,
    validate_inputs,
)
from graphwright.credentials import Credential, CredentialRejected
from graphwright.credentials.base import NoProvider
from graphwright.credentials.http import HttpLeaseProvider
from graphwright.credentials.local import EnvProvider, FileProvider
from graphwright.credentials.totp import seconds_left, totp
from graphwright.engine import Workflow
from graphwright.engine.secrets import Secret

SEED = "JBSWY3DPEHPK3PXP"


# --- totp ------------------------------------------------------------------------------


def test_totp_rfc6238_vectors_and_seed_formats() -> None:
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert totp(seed, at=59, digits=8) == "94287082"
    assert totp(seed, at=1111111109, digits=8) == "07081804"
    assert totp(seed.lower(), at=59, digits=8) == "94287082"  # case-insensitive
    assert totp("GEZD GNBV-GY3T QOJQ GEZD GNBV GY3T QOJQ", at=59, digits=8) == "94287082"
    assert len(totp(SEED)) == 6
    assert 0 < seconds_left(30, at=61) <= 30 and seconds_left(30, at=60) == 30
    with pytest.raises(ValueError, match="base32"):
        totp("not base32 !!")


# --- file / env ------------------------------------------------------------------------


def test_file_provider_reads_yaml_and_json(tmp_path) -> None:
    path = tmp_path / "creds.yaml"
    path.write_text(
        "demo:\n"
        "  kind: password\n"
        "  portal: acme-records\n"
        f"  totp_secret: {SEED}\n"
        "  values: {username: demo, password: demo-password}\n"
        "plain:\n"
        "  values: {token: abc12345}\n",
        encoding="utf-8",
    )
    cred = FileProvider(path).redeem("demo", run_id="r1")
    assert cred.portal == "acme-records" and cred.values == {"username": "demo", "password": "demo-password"}
    assert cred.totp is not None and cred.totp() == totp(SEED)
    assert "demo-password" not in repr(cred)  # values are excluded from the repr
    assert cred.plaintexts == ["demo", "demo-password"]
    assert FileProvider(path).redeem("plain", run_id="r1").totp is None

    with pytest.raises(CredentialRejected) as err:
        FileProvider(path).redeem("ghost", run_id="r1")
    assert err.value.code == "credential_not_found"

    js = tmp_path / "creds.json"
    js.write_text(json.dumps({"x": {"values": {"username": "u"}}}), encoding="utf-8")
    assert FileProvider(js).redeem("x", run_id="r").values == {"username": "u"}

    with pytest.raises(CredentialRejected) as err:
        FileProvider(tmp_path / "missing.yaml").redeem("x", run_id="r")
    assert err.value.code == "credentials_file_unreadable"
    (tmp_path / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(CredentialRejected):
        FileProvider(tmp_path / "list.yaml").redeem("a", run_id="r")


def test_env_provider_maps_refs_to_prefixed_variables() -> None:
    environ = {
        "GW_CRED_DEMO_USER_USERNAME": "demo",
        "GW_CRED_DEMO_USER_PASSWORD": "demo-password",
        "GW_CRED_DEMO_USER_TOTP_SECRET": SEED,
        "GW_CRED_DEMO_USER_PORTAL": "acme-records",
        "GW_CRED_OTHER_PASSWORD": "x",
    }
    cred = EnvProvider("GW_CRED_", environ).redeem("demo-user", run_id="r")
    assert cred.values == {"username": "demo", "password": "demo-password"}
    assert cred.portal == "acme-records" and cred.totp is not None and cred.totp() == totp(SEED)
    with pytest.raises(CredentialRejected):
        EnvProvider("GW_CRED_", environ).redeem("nobody", run_id="r")


def test_no_provider_refuses() -> None:
    with pytest.raises(CredentialRejected) as err:
        NoProvider().redeem("x", run_id="r")
    assert err.value.code == "no_credential_provider"


def test_get_provider_follows_settings_and_register_provider(monkeypatch) -> None:
    from graphwright.config import settings

    credentials.reset_provider()
    try:
        monkeypatch.setattr(settings, "credential_provider", "none")
        assert credentials.get_provider().name == "none"
        credentials.reset_provider()

        class Custom:
            name = "custom"

            def redeem(self, ref: str, *, run_id: str) -> Credential:
                return Credential(ref=ref, values={"token": "t-" + ref})

        credentials.register_provider("custom", Custom)
        monkeypatch.setattr(settings, "credential_provider", "custom")
        assert credentials.get_provider().redeem("a", run_id="r").values == {"token": "t-a"}
    finally:
        credentials.reset_provider()
        credentials._FACTORIES.pop("custom", None)


# --- http lease ------------------------------------------------------------------------


def test_http_provider_redeems_and_fetches_totp_per_call() -> None:
    calls: list[tuple[str, dict, dict]] = []
    codes = iter(["111111", "222222"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.url.path, body, dict(request.headers)))
        if request.url.path.endswith("/redeem"):
            return httpx.Response(200, json={
                "credential": {"kind": "password", "portal": "acme-records",
                               "values": {"username": "demo", "password": "pw-12345"}},
                "totp": True,
            })
        return httpx.Response(200, json={"code": next(codes)})

    p = HttpLeaseProvider("http://secrets.internal/v1/", key_id="worker-1", key="k3y",
                          transport=httpx.MockTransport(handler))
    cred = p.redeem("lease-abc", run_id="run-1")

    assert cred.values["password"] == "pw-12345" and cred.portal == "acme-records"
    path, body, headers = calls[0]
    assert path == "/v1/leases/lease-abc/redeem" and body == {"run_id": "run-1"}
    assert headers["x-key-id"] == "worker-1" and headers["x-key"] == "k3y"
    assert len(calls) == 1  # no code fetched ahead of time
    assert cred.totp() == "111111" and cred.totp() == "222222"
    assert [c[0] for c in calls[1:]] == ["/v1/leases/lease-abc/totp"] * 2


def test_http_provider_maps_errors_to_codes() -> None:
    def rejected(_: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={"detail": {"code": "lease_expired"}})

    with pytest.raises(CredentialRejected) as err:
        HttpLeaseProvider("http://s", transport=httpx.MockTransport(rejected)).redeem("l", run_id="r")
    assert err.value.code == "lease_expired" and "410" in str(err.value)

    def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(CredentialRejected) as err:
        HttpLeaseProvider("http://s", transport=httpx.MockTransport(broken)).redeem("l", run_id="r")
    assert err.value.code == "credentials_unavailable"

    def no_totp(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"credential": {"values": {"token": "t"}}})

    assert HttpLeaseProvider("http://s", transport=httpx.MockTransport(no_totp)).redeem("l", run_id="r").totp is None
    with pytest.raises(ValueError):
        HttpLeaseProvider("")


def test_client_certificates_from_pfx() -> None:
    pfx = b"\x30\x82fake-pkcs12"
    cred = Credential(ref="c", kind="certificate",
                      values={"pfx": base64.b64encode(pfx).decode(), "passphrase": "pass-1234"},
                      metadata={"origins": ["https://a.example", "https://b.example"]})
    certs = cred.client_certificates()
    assert certs == [
        {"origin": "https://a.example", "pfx": pfx, "passphrase": "pass-1234"},
        {"origin": "https://b.example", "pfx": pfx, "passphrase": "pass-1234"},
    ]
    assert cred.plaintexts == ["pass-1234"]  # the pfx blob is not a redaction target
    assert Credential(ref="x").client_certificates() is None


# --- catalog: credential + run inputs → graph inputs -----------------------------------


def _wf() -> Workflow:
    return Workflow.model_validate({
        "workflow": "t", "portal": "acme-records", "start": "a",
        "inputs": {
            "base_url": {"pii": False, "default": "http://demo-site:8090"},
            "reference": {"pii": False},
            "note": {"required": False},
            "username": {"credential": "username"},
            "password": {"credential": "password"},
        },
        "nodes": {"a": {"terminal": "success"}},
    })


def test_build_inputs_layers_defaults_run_values_and_credential_secrets() -> None:
    cred = Credential(ref="d", values={"username": "demo", "password": "demo-password"})
    out = build_inputs(_wf(), {"reference": "ACM-1", "username": "ignored"}, cred)
    assert out["base_url"] == "http://demo-site:8090" and out["reference"] == "ACM-1"
    assert isinstance(out["username"], Secret) and out["username"].reveal() == "demo"  # credential wins
    assert out["password"].reveal() == "demo-password"
    assert "note" not in out
    assert "demo-password" not in repr(out)

    with pytest.raises(InputsInvalid) as err:
        build_inputs(_wf(), {"reference": "ACM-1"}, None)
    assert "missing input 'username' (credential field)" in str(err.value)


def test_validate_inputs_is_what_the_api_checks() -> None:
    wf = _wf()
    assert validate_inputs(wf, {"reference": "x"}, has_credential=True) == []
    errors = validate_inputs(wf, {"password": "p", "bogus": 1}, has_credential=False)
    assert "unknown input 'bogus'" in errors
    assert "input 'password' comes from the credential, not from the run" in errors
    assert "input 'username' needs a credential (the run has none)" in errors
    assert "missing input 'reference'" in errors


def test_catalog_lists_and_loads_the_example_graphs(tmp_path) -> None:
    names = [g.name for g in list_graphs(GRAPHS)]
    assert {"acme_record_search", "acme_account_login", "acme_login_relay_otp"} <= set(names)
    info = get_graph("acme_account_login", GRAPHS)
    assert info.ok and info.requests == ["otp"] and info.inputs["password"]["credential"] == "password"
    assert load_graph("acme_record_search", GRAPHS).workflow.portal == "acme-records"

    with pytest.raises(GraphNotFound):
        load_graph("does_not_exist", GRAPHS)
    with pytest.raises(GraphNotFound):
        load_graph("../etc/passwd", GRAPHS)
    (tmp_path / "broken.yaml").write_text("workflow: [", encoding="utf-8")
    (tmp_path / "bad_lint.yaml").write_text(
        "workflow: bad_lint\nportal: p\nstart: a\nnodes:\n  a: {recognize: {always: true}, transitions: [{to: nope}]}\n",
        encoding="utf-8",
    )
    with pytest.raises(GraphInvalid):
        load_graph("broken", tmp_path)
    with pytest.raises(GraphInvalid) as err:
        load_graph("bad_lint", tmp_path)
    assert any("unknown node 'nope'" in e for e in err.value.errors)
    listed = {g.name: g for g in list_graphs(tmp_path)}
    assert not listed["broken"].ok and not listed["bad_lint"].ok
