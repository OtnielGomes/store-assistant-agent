from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from pydantic import PrivateAttr

Reply = str | AIMessage | Callable[[list[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Chat model that returns predetermined replies and records its input."""

    responses: list[Any]
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _index: int = PrivateAttr(default=0)

    def bind_tools(
        self,
        tools: Sequence[object],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        return self

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self._calls.append(list(messages))
        item: Reply = self.responses[self._index]
        self._index += 1
        if isinstance(item, str):
            message = AIMessage(content=item)
        elif isinstance(item, AIMessage):
            message = item
        elif callable(item):
            message = item(list(messages))
        else:
            raise TypeError(f"Unsupported scripted response: {type(item)!r}")
        return ChatResult(generations=[ChatGeneration(message=message)])
