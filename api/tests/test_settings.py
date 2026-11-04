import pytest
from pydantic import ValidationError

CONFIGURED_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_REASONING_EFFORT",
    "OPENAI_TEMPERATURE",
    "OPENAI_EMBEDDING_MODEL",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DATABASE_URL",
    "APP_ENV",
    "APP_TIMEZONE",
    "NEXT_PUBLIC_API_URL",
)


def _clear_configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CONFIGURED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_settings_use_documented_defaults_when_environment_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_configured_env(monkeypatch)

    from lia_api.settings import load_settings

    settings = load_settings()

    assert settings.openai_model == "gpt-6-luna"
    assert settings.openai_reasoning_effort == "none"
    assert settings.openai_temperature == 0
    assert settings.openai_embedding_model == "text-embedding-3-small"


def test_startup_rejects_temperature_when_reasoning_effort_is_not_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_configured_env(monkeypatch)
    monkeypatch.setenv("OPENAI_TEMPERATURE", "0.7")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")

    from lia_api.settings import load_settings

    with pytest.raises(ValidationError) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert "OPENAI_TEMPERATURE" in message
    assert "OPENAI_REASONING_EFFORT" in message
