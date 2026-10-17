"""OpenAI-compatible /v1/chat/completions (llama-server, DeepSeek, Groq, OpenAI)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import requests

from .base import AgentAborted, AssistantMessage, BaseProvider, Message, ToolCall

_REFUSED = (
    "10061",
    "10060",
    "actively refused",
    "connection refused",
    "failed to establish",
    "max retries exceeded",
    "newconnectionerror",
    "name or service not known",
    "nodename nor servname",
    "connectionabort",
)


def friendly_provider_error(
    exc: BaseException | str,
    *,
    display_name: str = "",
    hint: str = "",
) -> str:
    """Turn urllib3 / WinError dumps into a short line the user can act on."""
    text = str(exc or "").strip()
    if not text:
        return "The local model did not answer."
    low = text.lower()
    already = (
        "llama-server is not running" in low
        or "llama-server is up" in text.lower()
        or "hugging face hub" in low
    )
    if already and "httpconnectionpool" not in low and "winerror" not in low:
        return text
    local = (
        display_name == "Local"
        or "127.0.0.1" in text
        or "localhost" in low
        or ":8787" in text
    )
    refused = any(token in low or token in text for token in _REFUSED)
    if local and refused:
        return (
            "llama-server is not running, so there is no local model to talk to. "
            "Jinx starts it when Local is on and a GGUF is in the models folder. "
            "Open AI Models → Local and click Launch if it stayed offline, or pull a "
            "model from Hugging Face Hub first."
        )
    if refused:
        extra = f" {hint}" if hint else ""
        return f"{display_name or 'The model host'} could not be reached.{extra}".strip()
    if "httpconnectionpool" in low or "urllib3" in low or "winerror" in low:
        extra = f" {hint}" if hint else ""
        return f"{display_name or 'The model host'} could not be reached.{extra}".strip()
    return text


def fit_chat_messages(messages: list[Message]) -> list[Message]:
    """llama.cpp Jinja (Gemma, etc.) requires user/assistant alternation.

    A leading `system` role is fine for Llama. Stacked `user` turns — which
    happen when earlier replies failed and never landed in the session — are
    not. Merge systems into the first user turn and collapse same-role runs.
    """
    systems: list[str] = []
    rest: list[Message] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role == "system":
            text = str(msg.get("content") or "").strip()
            if text:
                systems.append(text)
            continue
        rest.append(msg)
    preamble = "\n\n".join(systems)
    out: list[Message] = []
    for msg in rest:
        role = str(msg.get("role") or "")
        can_merge = (
            out
            and role in {"user", "assistant"}
            and out[-1].get("role") == role
            and not msg.get("tool_calls")
            and not out[-1].get("tool_calls")
        )
        if can_merge:
            prev = str(out[-1].get("content") or "")
            nxt = str(msg.get("content") or "")
            merged = "\n\n".join(part for part in (prev, nxt) if part)
            out[-1] = {**out[-1], "content": merged}
            continue
        out.append(dict(msg))
    if preamble:
        if out and out[0].get("role") == "user":
            body = str(out[0].get("content") or "")
            out[0] = {**out[0], "content": preamble + ("\n\n" + body if body else "")}
        else:
            out.insert(0, {"role": "user", "content": preamble})
    if out and out[0].get("role") not in {"user", "tool"}:
        out.insert(0, {"role": "user", "content": preamble or "Hello"})
    return out or [{"role": "user", "content": preamble or "Hello"}]


def _http_error_text(response: requests.Response) -> str:
    body = (response.content or b"").decode("utf-8", errors="replace")[:1200]
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"]).strip()
        if isinstance(err, str) and err.strip():
            return err.strip()
        if payload.get("message"):
            return str(payload["message"]).strip()
    return body.strip() or f"HTTP {response.status_code}"


OnText = Callable[[str], None]
AbortFn = Callable[[], bool]


def _api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1") or root.endswith("/openai"):
        return root
    return root + "/v1"


def _utf8_lines(response: requests.Response):
    """SSE/NDJSON as UTF-8. requests otherwise uses ISO-8859-1 and mangles quotes."""
    response.encoding = "utf-8"
    for raw in response.iter_lines(decode_unicode=False):
        if not raw:
            continue
        yield raw.decode("utf-8", errors="replace")


def _parse_tools(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for item in message.get("tool_calls") or []:
        fn = item.get("function") or {}
        raw_args = fn.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args or {}
        if not isinstance(args, dict):
            args = {}
        calls.append(ToolCall(name=fn.get("name", ""), arguments=args, id=item.get("id")))
    return calls


def _probe_model(api_root: str, timeout: int = 3) -> str:
    try:
        response = requests.get(f"{api_root}/models", timeout=timeout)
        response.raise_for_status()
        items = (response.json().get("data") or [])
        if items:
            return str(items[0].get("id") or "local-model")
    except requests.RequestException:
        pass
    return "local-model"


class OpenAICompatProvider(BaseProvider):
    display_name = "OpenAI-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = "",
        timeout: int = 180,
        extra_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        error_hint: str = "",
        display_name: str | None = None,
    ) -> None:
        self.api_root = _api_root(base_url)
        self.api_key = api_key
        self.model = model or _probe_model(self.api_root)
        self.timeout = timeout
        self.extra_body = extra_body or {}
        self.error_hint = error_hint
        self._headers = headers or {}
        if display_name:
            self.display_name = display_name
        self._response: requests.Response | None = None

    def _headers_for_call(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key or 'none'}",
            "Content-Type": "application/json",
            **self._headers,
        }
        return headers

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            **self.extra_body,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _wrap_request(self, exc: BaseException) -> RuntimeError:
        return RuntimeError(
            friendly_provider_error(exc, display_name=self.display_name, hint=self.error_hint)
        )

    def _raise_http(self, response: requests.Response) -> None:
        detail = _http_error_text(response)
        low = detail.lower()
        if self.display_name == "Local":
            if "not found" in low:
                name = self.model or "(none)"
                raise RuntimeError(
                    f"llama-server has no GGUF named {name}. "
                    "Open AI Models → Local and click Load, or pick that model on General."
                )
            if "missing from the request" in low:
                raise RuntimeError(
                    "No local model name was sent. Pick a GGUF on AI Models → General."
                )
            short = detail
            if "error:" in low:
                short = detail[low.rfind("error:") + 6 :].strip()
            short = short.splitlines()[0][:240]
            raise RuntimeError(
                f"Local model error HTTP {response.status_code}: {short}"
            )
        hint = f" {self.error_hint}" if self.error_hint else ""
        raise RuntimeError(
            f"{self.display_name} HTTP {response.status_code}: {detail[:800]}{hint}"
        )

    def close_stream(self) -> None:
        response = self._response
        self._response = None
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        try:
            response = requests.post(
                f"{self.api_root}/chat/completions",
                json=self._payload(messages, tools, stream=False),
                headers=self._headers_for_call(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise self._wrap_request(exc) from None
        response.encoding = "utf-8"
        if response.status_code >= 400:
            self._raise_http(response)
        message = response.json()["choices"][0]["message"]
        content = message.get("content")
        if isinstance(content, str):
            from ..activity import repair_utf8_text

            content = repair_utf8_text(content)
            message["content"] = content
        return AssistantMessage(
            content=content,
            tool_calls=_parse_tools(message),
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
        try:
            response = requests.post(
                f"{self.api_root}/chat/completions",
                json=self._payload(messages, tools, stream=True),
                headers=self._headers_for_call(),
                timeout=(10, self.timeout),
                stream=True,
            )
        except requests.RequestException as exc:
            raise self._wrap_request(exc) from None
        self._response = response
        if response.status_code >= 400:
            self.close_stream()
            self._raise_http(response)

        from ..activity import repair_utf8_text

        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        try:
            for raw_line in _utf8_lines(response):
                if abort():
                    break
                if not raw_line:
                    continue
                line = raw_line.strip()
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    piece = repair_utf8_text(str(piece))
                    content_parts.append(piece)
                    if on_text:
                        on_text(piece)
                for call in delta.get("tool_calls") or []:
                    index = int(call.get("index") or 0)
                    slot = tool_acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if call.get("id"):
                        slot["id"] = str(call["id"])
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += str(fn["name"])
                    if fn.get("arguments"):
                        slot["arguments"] += str(fn["arguments"])
        except AgentAborted:
            if abort():
                text = "".join(content_parts) or None
                return AssistantMessage(content=text, tool_calls=[], raw={})
            raise
        except requests.RequestException as exc:
            if abort():
                text = "".join(content_parts) or None
                return AssistantMessage(content=text, tool_calls=[], raw={})
            raise self._wrap_request(exc) from None
        finally:
            self.close_stream()

        tool_calls: list[ToolCall] = []
        for index in sorted(tool_acc):
            slot = tool_acc[index]
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                ToolCall(name=slot["name"], arguments=args, id=slot["id"] or None)
            )
        text = "".join(content_parts) or None
        raw = {"role": "assistant", "content": text, "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in tool_calls
        ] if tool_calls else None}
        return AssistantMessage(content=text, tool_calls=tool_calls, raw=raw)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


class DeepSeekProvider(OpenAICompatProvider):
    display_name = "DeepSeek V4 Pro"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com",
        timeout: int = 180,
    ) -> None:
        import os

        key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        super().__init__(
            base_url=base_url,
            api_key=key,
            model=model,
            timeout=timeout,
            extra_body={"thinking": {"type": "disabled"}},
            display_name="DeepSeek V4 Pro",
        )

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        if not self.api_key:
            raise RuntimeError(
                "Missing DeepSeek API key. Add it in Settings → API Access."
            )
        return super().chat(messages, tools)

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_text: OnText | None = None,
        abort: AbortFn | None = None,
    ) -> AssistantMessage:
        if not self.api_key:
            raise RuntimeError(
                "Missing DeepSeek API key. Add it in Settings → API Access."
            )
        return super().stream_chat(messages, tools, on_text, abort)


class LMStudioProvider(OpenAICompatProvider):
    display_name = "LM Studio"

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "",
        timeout: int = 180,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key="lm-studio",
            model=model,
            timeout=timeout,
            extra_body={"temperature": 0.2},
            display_name="LM Studio",
            error_hint="Is LM Studio running, with a model loaded and the local server started?",
        )


class LocalLlamaProvider(OpenAICompatProvider):
    display_name = "Local"

    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        timeout: int = 180,
    ) -> None:
        from ..config import LLAMA_SERVER_URL

        super().__init__(
            base_url=base_url or LLAMA_SERVER_URL,
            api_key="jinx",
            model=model,
            timeout=timeout,
            extra_body={"temperature": 0.2},
            display_name="Local",
            error_hint="Jinx will start llama-server when you chat. Pull a GGUF from Hugging Face Hub if the folder is empty.",
        )

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload = super()._payload(fit_chat_messages(list(messages)), tools, stream)
        if not str(payload.get("model") or "").strip():
            from ..llama_server import list_loaded

            loaded = list_loaded()
            if loaded:
                self.model = loaded[0]
                payload["model"] = loaded[0]
        return payload

    def _ensure_host(self) -> None:
        from ..llama_server import ensure_ready

        ensure_ready(self.model)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        self._ensure_host()
        return super().chat(messages, tools)

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_text: OnText | None = None,
        abort: AbortFn | None = None,
    ) -> AssistantMessage:
        self._ensure_host()
        return super().stream_chat(messages, tools, on_text, abort)


class OpenRouterProvider(OpenAICompatProvider):
    display_name = "OpenRouter"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: int = 180,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model or "openrouter/auto",
            timeout=timeout,
            headers={"HTTP-Referer": "https://jinx-ai.local", "X-Title": "Jinx AI"},
            display_name="OpenRouter",
            error_hint="Set JINX_OPENROUTER_KEY and JINX_OPENROUTER_MODEL in .env.",
        )


class OpenAIProvider(OpenAICompatProvider):
    display_name = "OpenAI"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 180,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model or "gpt-4o-mini",
            timeout=timeout,
            display_name="OpenAI",
            error_hint="Set JINX_OPENAI_KEY and JINX_OPENAI_MODEL in .env.",
        )
