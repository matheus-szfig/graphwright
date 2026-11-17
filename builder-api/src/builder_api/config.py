"""Builder-specific configuration (BUILDER_*). The engine keeps its own settings."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUILDER_", extra="ignore")
    ai_openai_key: SecretStr | None = None
    ai_anthropic_key: SecretStr | None = None
    ai_docs_dir: str | None = None
    ai_connection_ttl_s: int = Field(default=3600, ge=60, le=86400)
    ai_max_rounds: int = Field(default=8, ge=1, le=20)
    ai_max_output_tokens: int = Field(default=0, ge=0, le=128000)  # 0: provider/model-aware initial budget
    ai_output_token_ceiling: int = Field(default=128000, ge=256, le=128000)
    ai_timeout_s: int = Field(default=0, ge=0, le=604800)  # 0: no generation deadline
    ai_context_chars: int = Field(default=16000, ge=4000, le=64000)
    ai_tool_page_chars: int = Field(default=6000, ge=1000, le=16000)
    ai_request_chars: int = Field(default=96000, ge=48000, le=500000)
    ai_triage_timeout_s: int = Field(default=20, ge=1, le=120)
    # Off: the main model explores on demand; a pre-selection model call is opt-in (D-44).
    ai_triage_auto: bool = False
    ai_triage_anthropic_model: str = "claude-haiku-5-5"
    ai_triage_openai_model: str = "gpt-5-nano"
    # Model picked at connect time when the catalog has it; switchable mid-conversation in the chat.
    ai_default_anthropic_model: str = "claude-opus-5-5"
    ai_default_openai_model: str = "gpt-5"
    ai_allowed_origins: str = "http://localhost:5174,http://localhost:8080,http://127.0.0.1:5174,http://127.0.0.1:8080"


settings = Settings()
