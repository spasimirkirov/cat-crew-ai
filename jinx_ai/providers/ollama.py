"""Ollama local-server provider."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import requests

from .base import AgentAborted, AssistantMessage, BaseProvider, Message, ToolCall

OnText = Callable[[str], None]
AbortFn = Callable[[], bool]


class OllamaProvider(BaseProvider):
    display_name = "Ollama (qwen2.5-coder:14b)"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5-coder:14b",
        timeout: int = 180,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._response: requests.Response | None = None

    def close_stream(self) -> None:
        response = self._response
        self._response = None
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    def _parse_tools(self, message: dict[str, Any]) -> list[ToolCall]:
        tool_calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                ToolCall(name=fn.get("name", ""), arguments=args, id=tc.get("id"))
            )
        return tool_calls

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        if tools:
            payload["tools"] = tools

        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        message = response.json()["message"]
        return AssistantMessage(
            content=message.get("content"),
            tool_calls=self._parse_tools(message),
            raw=message,
        )

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_text: OnText | None = None,
        abort: AbortFn | None = None,
    ) -> AssistantMessage:
        abort = abort or (lambda: False)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.2},
        }
        if tools:
            payload["tools"] = tools
        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=(10, self.timeout),
            stream=True,
        )
        self._response = response
        response.raise_for_status()
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw: dict[str, Any] = {}
        try:
            for line in response.iter_lines(decode_unicode=False):
                if abort():
                    break
                if not line:
                    continue
                try:
                    data = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                message = data.get("message") or {}
                raw = message or raw
                piece = message.get("content") or ""
                if piece:
                    content_parts.append(piece)
                    if on_text:
                        on_text(piece)
                parsed = self._parse_tools(message)
                if parsed:
                    tool_calls = parsed
                if data.get("done"):
                    break
        except (requests.RequestException, AgentAborted):
            if abort():
                text = "".join(content_parts) or None
                return AssistantMessage(content=text, tool_calls=[], raw={})
            raise
        finally:
            self.close_stream()
        text = "".join(content_parts) or None
        return AssistantMessage(
            content=text,
            tool_calls=tool_calls,
            raw=raw or {"role": "assistant", "content": text},
        )

    def assistant_tool_turn(self, reply: AssistantMessage) -> Message:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": call.name, "arguments": call.arguments}}
                for call in reply.tool_calls
            ],
        }

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "content": result}
