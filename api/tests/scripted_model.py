from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr


class ScriptedToolCall(BaseModel):
    """A predetermined tool call emitted before the next scripted reply."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)


Reply = str | AIMessage | ScriptedToolCall | Callable[[list[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Chat model that returns predetermined replies and records its input."""

    responses: list[Any]
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _index: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=tools, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self._calls.append(list(messages))
        step: Reply = self.responses[self._index]
        self._index += 1
        if isinstance(step, ScriptedToolCall):
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": step.name,
                        "args": step.args,
                        "id": f"call_{self._index}",
                        "type": "tool_call",
                    }
                ],
            )
        elif isinstance(step, str):
            message = AIMessage(content=step)
        elif isinstance(step, AIMessage):
            message = step
        elif callable(step):
            message = step(list(messages))
        else:
            raise TypeError(f"Unsupported scripted response: {type(step)!r}")
        return ChatResult(generations=[ChatGeneration(message=message)])
