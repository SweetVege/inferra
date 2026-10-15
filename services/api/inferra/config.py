from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with INFERRA_."""

    model_config = SettingsConfigDict(env_prefix="INFERRA_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./inferra-dev.db"
    secret_key: str = "dev-secret-change-me"
    web_origin: str = "http://localhost:3000"

    # Storage: "local" keeps files under data_dir; "s3" uses any S3-compatible bucket (e.g. R2).
    storage_backend: Literal["local", "s3"] = "local"
    data_dir: Path = Path("./.inferra-data")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_region: str = "auto"

    # GitHub OAuth. When unset outside production, a local dev login is offered instead.
    github_client_id: str | None = None
    github_client_secret: str | None = None

    # LLM. The model id is required to enable agent runs; ANTHROPIC_API_KEY is read by the SDK.
    llm_model: str | None = None
    llm_effort_planning: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_effort_specialist: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_effort_insight: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_max_tokens: int = 16000

    # Runtime guardrails.
    inline_worker: bool = True
    max_tool_calls_per_step: int = 8
    max_insight_revisions: int = 2
    max_upload_mb: int = 50
    sql_row_limit: int = 200
    extract_max_rows: int = 2_000_000
    allow_private_db_hosts: bool | None = None  # defaults to True outside production

    def model_post_init(self, _context: object) -> None:
        if self.allow_private_db_hosts is None:
            self.allow_private_db_hosts = self.env != "production"

    @property
    def dev_login_enabled(self) -> bool:
        return self.env != "production" and not self.github_client_id

    @property
    def secure_cookies(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
