from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field, PrivateAttr


class ScriptedToolCall(BaseModel):
    """A predetermined tool call emitted by the fake chat model."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ScriptedChatModel(BaseChatModel):
    """Chat model that returns predetermined replies and records its input."""

    responses: list[str | ScriptedToolCall]
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _index: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @property
    def calls(self) -> list[list[BaseMessage]]:
        return self._calls

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self._calls.append(list(messages))
        step = self.responses[self._index]
        self._index += 1
        if isinstance(step, ScriptedToolCall):
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": step.name,
                        "args": step.args,
                        "id": f"call-{self._index}",
                        "type": "tool_call",
                    }
                ],
            )
        elif step == "__echo_tool__":
            tool_messages = [item for item in messages if isinstance(item, ToolMessage)]
            text = str(tool_messages[-1].content) if tool_messages else ""
            message = AIMessage(content=text)
        else:
            message = AIMessage(content=str(step))
        return ChatResult(generations=[ChatGeneration(message=message)])
