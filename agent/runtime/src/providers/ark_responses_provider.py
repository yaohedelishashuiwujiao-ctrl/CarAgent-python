"""Volcengine Ark provider backed by the OpenAI Responses API."""

from __future__ import annotations

import json
from typing import Any, Generator, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .base import BaseProvider, ChatResponse, MessageInput
from .http_config import openai_http_options


def _tool_schema(tool: dict[str, Any]) -> dict[str, Any] | None:
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        return None
    return {
        "type": "function",
        "name": str(tool.get("name") or ""),
        "description": str(tool.get("description") or ""),
        "parameters": schema,
    }


def _message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _responses_input(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = _message_content(message.get("content") or "")
        if role == "system":
            if content:
                instructions.append(content)
            continue
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": content,
            })
            continue
        if content:
            items.append({"role": role, "content": content})
        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                items.append({
                    "type": "function_call",
                    "call_id": str(tool_call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "arguments": str(function.get("arguments") or "{}"),
                })
    return ("\n\n".join(instructions) or None), items


class ArkResponsesProvider(BaseProvider):
    """Use Ark models that are exposed only through ``client.responses``."""

    # The current Doubao Responses deployment rejects both the OpenAI
    # ``"required"`` sentinel and the explicit function-choice object with
    # InvalidParameter.  Do not infer wire compatibility from the OpenAI SDK:
    # advertise no forced-choice modes and let Runtime use candidate narrowing
    # plus its explicit planning/completion guidance instead.
    tool_choice_modes = frozenset()

    def format_tool_choice(self, mode: str, tool_name: str | None = None) -> Any | None:
        if mode not in self.tool_choice_modes:
            return None
        if mode == "specific":
            if not tool_name:
                return None
            return {"type": "function", "name": tool_name}
        return mode

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(
            api_key,
            base_url or "https://ark.cn-beijing.volces.com/api/v3",
            model or "doubao-seed-evolving",
        )
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            if OpenAI is None:  # pragma: no cover
                raise ModuleNotFoundError("openai package is required")
            kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "base_url": self.base_url,
                **openai_http_options(),
            }
            self._client = OpenAI(**kwargs)
        return self._client

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        prepared = self._prepare_messages(messages)
        instructions, input_items = _responses_input(prepared)
        request: dict[str, Any] = {
            "model": self._get_model(**kwargs),
            "input": input_items,
        }
        if instructions:
            request["instructions"] = instructions
        if tools:
            request["tools"] = [item for tool in tools if (item := _tool_schema(tool))]
        if kwargs.get("tool_choice") is not None:
            request["tool_choice"] = kwargs["tool_choice"]

        response = self.client.responses.create(**request)
        tool_uses: list[dict[str, Any]] = []
        reasoning_parts: list[str] = []
        for item in response.output:
            item_type = getattr(item, "type", "")
            if item_type == "function_call":
                try:
                    arguments = json.loads(getattr(item, "arguments", "{}") or "{}")
                except Exception:
                    arguments = {}
                tool_uses.append({
                    "id": str(getattr(item, "call_id", None) or getattr(item, "id", "")),
                    "name": str(getattr(item, "name", "")),
                    "input": arguments,
                })
            elif item_type == "reasoning":
                for summary in getattr(item, "summary", None) or []:
                    text = getattr(summary, "text", None)
                    if text:
                        reasoning_parts.append(str(text))

        usage = getattr(response, "usage", None)
        usage_dict = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        return ChatResponse(
            content=str(getattr(response, "output_text", "") or ""),
            model=str(getattr(response, "model", self.model) or self.model),
            usage=usage_dict,
            finish_reason="tool_use" if tool_uses else str(getattr(response, "status", "stop")),
            reasoning_content="\n".join(reasoning_parts) or None,
            tool_uses=tool_uses or None,
        )

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Generator[str, None, None]:
        response = self.chat(messages, tools=tools, **kwargs)
        if response.content:
            yield response.content

    def get_available_models(self) -> list[str]:
        return ["doubao-seed-evolving"]
