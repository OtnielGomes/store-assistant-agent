import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from lia_api.settings import Settings


def configure_langsmith(settings: Settings) -> None:
    """Publish LangSmith settings into the environment the SDK reads."""
    os.environ["LANGSMITH_TRACING"] = "true" if settings.langsmith_tracing else "false"
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key


def build_chat_model(settings: Settings) -> BaseChatModel:
    """OpenAI chat model using the Responses API."""
    options: dict[str, object] = {
        "model": settings.openai_model,
        "api_key": settings.openai_api_key or None,
        "use_responses_api": True,
    }
    if settings.openai_reasoning_effort == "none":
        # gpt-6-luna rejects temperature=0; omit unless explicitly configured.
        if "openai_temperature" in settings.model_fields_set:
            options["temperature"] = settings.openai_temperature
    else:
        options["reasoning_effort"] = settings.openai_reasoning_effort
    return ChatOpenAI(**options)
