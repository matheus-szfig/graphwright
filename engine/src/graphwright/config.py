"""Single env-var boundary for the engine and worker. No ``os.environ`` anywhere else."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

HeadlessMode = Literal["true", "false", "new"]
CredentialProviderName = Literal["none", "file", "env", "http"]
EvidenceMode = Literal["failure", "always", "never"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    redis_url: str = Field(default="redis://localhost:6379/0")
    # The worker's role (graphwright_worker) can read runs and write their outcome,
    # events and artifacts — nothing else. See api/alembic.
    postgres_dsn: str = Field(default="postgresql://graphwright_worker:graphwright_worker@localhost:5432/graphwright")

    # Any S3-compatible store; compose runs RustFS.
    s3_endpoint: str = Field(default="http://localhost:9000")
    s3_access_key: str = Field(default="graphwright")
    s3_secret_key: str = Field(default="graphwright-dev-only")
    s3_region: str = Field(default="us-east-1")
    s3_bucket_prefix: str = Field(default="graphwright-dev")

    # Failure evidence (graphwright/evidence.py): what the worker captures from the
    # live page when a run fails — screenshot, redacted HTML, the last PageState and
    # a context record — stored in <prefix>-<suffix> with one `artifact` row each.
    # "failure" = failed runs and `terminal: failure` outcomes; "always" also on success.
    evidence_on: EvidenceMode = Field(default="failure")
    evidence_bucket_suffix: str = Field(default="evidence")
    evidence_screenshot: bool = Field(default=True)
    evidence_full_page: bool = Field(default=True)
    evidence_max_bytes: int = Field(default=5_000_000)  # per object; larger HTML is truncated

    browser_headless: HeadlessMode = Field(default="true")
    browser_default_profile: str = Field(default="win11_chrome_1920")
    browser_telemetry_block_enabled: bool = Field(default=False)
    browser_session_bucket_suffix: str = Field(default="sessions")
    # Dev escape hatch — Chromium skips cert chain validation (self-signed staging).
    # Keep False in production; runs that carry a credential refuse to start with it on.
    browser_ignore_https_errors: bool = Field(default=False)

    # Where runs load graphs from; empty = <engine>/graphs (what the image ships).
    graphs_dir: str = Field(default="")
    # Comma-separated modules imported at startup to register shapers / portal aliases
    # (graphwright/plugins.py), e.g. "acme_shapers,acme_portals".
    plugins: str = Field(default="")

    # Credentials (graphwright/credentials/). A run names a credential by reference;
    # the provider turns that reference into values at the moment of use.
    credential_provider: CredentialProviderName = Field(default="none")
    # file provider: YAML/JSON mapping ref → {kind, portal, values, totp_secret, …}
    credentials_file: str = Field(default="")
    # env provider: GW_CRED_<REF>_<FIELD>=value
    credentials_env_prefix: str = Field(default="GW_CRED_")
    # http provider: a lease service (POST {url}/leases/{ref}/redeem, …/totp)
    credentials_http_url: str = Field(default="")
    credentials_http_key_id: str = Field(default="")
    credentials_http_key: str = Field(default="")
    # mTLS: client cert (PEM path or "cert.pem,key.pem") and CA bundle to verify the service
    credentials_http_client_cert: str = Field(default="")
    credentials_http_ca_bundle: str = Field(default="")

    # Graph requests (engine/requests.py). Empty = any host; otherwise a comma-separated
    # allowlist of hostnames (exact, or ".example.com" for a suffix) requests may reach.
    requests_allowed_hosts: str = Field(default="")
    requests_default_timeout_s: float = Field(default=10.0)


settings = Settings()
