from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_api_key: SecretStr
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "store-assistant"
    langsmith_tracing: bool = False

    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    app_log_level: str = "INFO"
    app_passphrase: SecretStr
    app_llm_model: str = "claude-sonnet-4-5"
    app_max_graph_iterations: int = 20
    # Off-scope strikes after which the agent terminates the conversation.
    # Surfaced in the Active guardrails sidebar badge so the value never
    # gets hard-coded into the UI copy (see T612).
    app_off_scope_threshold: int = 3

    # Separate connection string for the LangGraph checkpoint store. Optional
    # at the Settings layer so tests don't have to provide it; production
    # entrypoints (CLI, Streamlit) require it and fail fast at startup if
    # unset (see agent/checkpointer.py).
    agent_checkpoint_db_url: SecretStr | None = None

    @property
    def database_url(self) -> str:
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
