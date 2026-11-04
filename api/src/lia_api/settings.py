from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["none", "low", "medium", "high"]

_PACKAGE_DIR = Path(__file__).resolve().parent
_ENV_CANDIDATES = (
    _PACKAGE_DIR.parents[2] / ".env",
    _PACKAGE_DIR.parents[1] / ".env",
)


def _env_files() -> tuple[str, ...]:
    return tuple(str(path) for path in _ENV_CANDIDATES if path.is_file())


class Settings(BaseSettings):
    """Process configuration loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=_env_files() or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    openai_reasoning_effort: ReasoningEffort = "none"
    openai_temperature: float = 0
    openai_embedding_model: str = "text-embedding-3-small"
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "emporio-da-musica"
    postgres_user: str = "lia"
    postgres_password: str = "lia"
    postgres_db: str = "lia"
    database_url: str = ""
    app_env: str = "development"
    app_timezone: str = "America/Campo_Grande"
    next_public_api_url: str = "http://127.0.0.1:8000"

    @model_validator(mode="after")
    def reject_temperature_unless_reasoning_effort_is_none(self) -> Self:
        temperature_was_configured = "openai_temperature" in self.model_fields_set
        if temperature_was_configured and self.openai_reasoning_effort != "none":
            raise ValueError(
                "OPENAI_TEMPERATURE cannot be set when OPENAI_REASONING_EFFORT "
                "is not 'none'. The OpenAI API rejects that combination."
            )
        return self


def load_settings() -> Settings:
    """Load settings and fail startup when sampling options are incompatible."""
    return Settings()
