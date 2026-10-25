"""Single env-var boundary for the runs API. No ``os.environ`` anywhere else."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_ENVS = frozenset({"dev", "test"})
DEV_KEY_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    env: str = Field(default="dev")  # dev | test | prod — anything else counts as prod
    # The schema owner: the API reads/writes runs and runs the migrations.
    postgres_dsn: str = Field(default="postgresql://graphwright:graphwright@localhost:5432/graphwright")
    redis_url: str = Field(default="redis://localhost:6379/0")
    # "id:secret,id2:secret2". The id is recorded as run.requested_by.
    api_keys: str = Field(default="")
    # Empty = the engine's default (<engine>/graphs, or the engine's own GRAPHS_DIR).
    graphs_dir: str = Field(default="")
    plugins: str = Field(default="")
    # Migration-time only: the password for the worker's least-privilege role.
    db_password_worker: str = Field(default="")

    @property
    def is_dev(self) -> bool:
        return self.env.lower() in DEV_ENVS

    def keys(self) -> dict[str, str]:
        """Parsed API_KEYS: id → secret. Refuses the dev placeholder outside dev."""
        out: dict[str, str] = {}
        for entry in (e.strip() for e in self.api_keys.split(",")):
            if not entry:
                continue
            key_id, sep, secret = entry.partition(":")
            if not sep or not key_id or not secret:
                raise ValueError(f"API_KEYS entry {key_id or entry[:8]!r} is not id:secret")
            if secret == DEV_KEY_SECRET and not self.is_dev:
                raise ValueError(f"API_KEYS entry {key_id!r} uses the dev placeholder secret outside dev")
            out[key_id] = secret
        return out


settings = Settings()
