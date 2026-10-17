"""OpenAI-compatible /v1/chat/completions: OpenRouter and the vendors' own APIs (OpenAI, Anthropic,
Gemini, DeepSeek, Groq, Mistral, xAI) -- every model host CatCrewAI talks to."""

from __future__ import annotations

import json
import threading
import time
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
        return f"{display_name or 'The model host'} did not answer."
    low = text.lower()
    refused = any(token in low or token in text for token in _REFUSED)
    if refused:
        extra = f" {hint}" if hint else ""
        return f"{display_name or 'The model host'} could not be reached.{extra}".strip()
    if "httpconnectionpool" in low or "urllib3" in low or "winerror" in low:
        extra = f" {hint}" if hint else ""
        return f"{display_name or 'The model host'} could not be reached.{extra}".strip()
    return text


def _status_hint(status: int, model: str, key_hint: str) -> str:
    """What the person can do about this status. A 429 is not a key problem: telling them to check
    their key sent them looking in the wrong place."""
    if status in (401, 403):
        return f" {key_hint}" if key_hint else ""
    if status == 402:
        return " The account is out of credit: add credit at the host, or pick a free model."
    if status == 429:
        if str(model or "").endswith(":free"):
            return (
                f" {model} is a free model: OpenRouter allows free models about 20 requests a minute and "
                "50 a day (1,000 a day with $10 of credit), and the upstream provider may throttle them "
                "further. A crew run makes many requests -- pick a paid model on the Models page for "
                "real work, or wait and try again."
            )
        return " The host is rate-limiting these requests: wait a minute and try again, or pick another model."
    return ""


def _http_error_text(response: requests.Response) -> str:
    body = (response.content or b"").decode("utf-8", errors="replace")[:1200]
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"]).strip()
            # OpenRouter puts the upstream provider's own words in metadata.raw
            # ("... is temporarily rate-limited upstream", "free-models-per-day").
            meta = err.get("metadata") if isinstance(err.get("metadata"), dict) else {}
            raw = str(meta.get("raw") or "").strip()
            who = str(meta.get("provider_name") or "").strip()
            if raw and raw not in message:
                message += f" ({who + ': ' if who else ''}{raw[:300]})"
            return message
        if isinstance(err, str) and err.strip():
            return err.strip()
        if payload.get("message"):
            return str(payload["message"]).strip()
    return body.strip() or f"HTTP {response.status_code}"


OnText = Callable[[str], None]
AbortFn = Callable[[], bool]


def _record(
    provider: "OpenAICompatProvider",
    messages: list[Message],
    content: str | None,
    calls: list[ToolCall],
    usage: dict[str, Any] | None,
    seconds: float,
) -> None:
    """Tell the usage meter about one finished request (requests / tokens per minute, speed)."""
    from ..services import usage_meter

    arg_chars = sum(len(json.dumps(call.arguments, ensure_ascii=False)) for call in calls)
    usage_meter.record_call(provider, messages, content=content, tool_arg_chars=arg_chars, usage=usage, seconds=seconds)


def _api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1") or root.endswith("/openai"):
        return root
    return root + "/v1"


def _merge_reasoning_details(acc: dict[int, dict[str, Any]], items: Any) -> None:
    """Stitch streamed ``reasoning_details`` pieces (OpenRouter) back into whole blocks by index."""
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        block = acc.setdefault(index, {})
        for key, value in item.items():
            if key in ("text", "summary", "data") and isinstance(value, str) and isinstance(block.get(key), str):
                block[key] += value
            elif value is not None:
                block[key] = value


def _close_quietly(response: requests.Response) -> None:
    try:
        response.close()
    except Exception:  # noqa: BLE001
        pass


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


def _rolling_breakpoint(model: str) -> bool:
    """Models whose explicit cache breakpoints add up (Anthropic's Claude family)."""
    slug = str(model or "").lower()
    return slug.startswith("anthropic/") or "/claude" in slug or slug.startswith("claude")


_RETRIES = 3
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_MAX_DELAY = 30.0


def _retries_for(response: requests.Response, model: str) -> int:
    """How many more tries a refused request deserves. A host hiccup (5xx): a few. A rate limit
    (429): one short retry on a paid model, none on a ``:free`` one -- free limits last minutes to a
    day, and retrying only burns the person's time before the switch-model offer appears."""
    if response.status_code != 429:
        return _RETRIES
    if str(model or "").endswith(":free"):
        return 0
    wait = _retry_after(response)
    return 1 if wait is None or wait <= 20 else 0


def _backoff(attempt: int) -> float:
    import random

    return min(_MAX_DELAY, 1.5 * (2 ** attempt)) * (0.75 + random.random() * 0.5)


def _retry_after(response: requests.Response) -> float | None:
    headers = getattr(response, "headers", None) or {}
    raw = str(headers.get("retry-after") or headers.get("Retry-After") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(0.0, min(value, _MAX_DELAY))


def _sleep_unless(abort: Callable[[], bool], seconds: float) -> bool:
    """Sleep in small steps; True if ``abort`` fired meanwhile (Stop must not wait out a backoff)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if abort():
            return True
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))
    return abort()


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
        host_id: str | None = None,
        stream_usage: bool = False,
        enable_prompt_caching: bool = False,
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
        if host_id:
            self.host_id = host_id
        # Ask a streaming host to append its token counts (``stream_options``). Only for hosts known to accept it.
        self.stream_usage = stream_usage
        # Anthropic-style ``cache_control`` breakpoint on the system prompt (OpenRouter's convention
        # for prompt caching). A harmless extra field on providers that ignore it.
        self.enable_prompt_caching = enable_prompt_caching
        # Every stream this provider has open. One provider object can serve two calls at once (a
        # specialist and a background summary): each call closes only its own stream when it ends,
        # and Stop (``close_stream``) closes them all. A single shared slot let one call's cleanup
        # cut another's reply off mid-read ("'NoneType' object has no attribute 'read'").
        self._streams: set[requests.Response] = set()
        self._streams_lock = threading.Lock()
        #: The last request's token counts as the host reported them ({"prompt_tokens", "completion_tokens"}), else None.
        self.last_usage: dict[str, Any] | None = None

    def _headers_for_call(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key or 'none'}",
            "Content-Type": "application/json",
            **self._headers,
        }
        return headers

    def _cache_messages(self, messages: list[Message]) -> list[Message]:
        """A copy of ``messages`` with a ``cache_control`` breakpoint on the system prompt (OpenRouter's
        convention for prompt caching, native to Anthropic-family models and ignored elsewhere). Never
        mutates the caller's list: the same ``messages`` object is reused across rounds and, mid-run,
        may be handed to a different provider that must see it unchanged."""
        if not messages or messages[0].get("role") != "system":
            return messages
        content = messages[0].get("content")
        if not isinstance(content, str) or not content:
            return messages
        cached_system = {**messages[0], "content": [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]}
        out = [cached_system, *messages[1:]]
        if _rolling_breakpoint(self.model):
            # A second, moving breakpoint on the newest message: each tool round then reads the
            # whole transcript so far from the cache and pays full price only for the new tail.
            # (Claude takes up to four breakpoints; Gemini uses only the last one, so it keeps the
            # single breakpoint on the system prompt, which stays stable across turns.)
            for index in range(len(out) - 1, 0, -1):
                tail = out[index]
                text = tail.get("content")
                if tail.get("role") in {"user", "tool"} and isinstance(text, str) and text.strip():
                    out[index] = {**tail, "content": [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]}
                    break
        return out

    def _payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._cache_messages(messages) if self.enable_prompt_caching else messages,
            "stream": stream,
            **self.extra_body,
        }
        if stream and self.stream_usage:
            payload["stream_options"] = {"include_usage": True}
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
        from ..services import usage_meter

        usage_meter.record_error(self, response.status_code, detail)  # a 429 is what the quota meter must show
        if response.status_code == 404 and ("model" in low or "not found" in low):
            raise RuntimeError(
                f"{self.display_name} has no model named {self.model or '(none)'}: {detail[:300]} "
                "Pick a model this host lists on the Models page."
            )
        hint = _status_hint(response.status_code, self.model, self.error_hint)
        message = f"{self.display_name} HTTP {response.status_code}: {detail[:800]}{hint}"
        if response.status_code in (402, 429):
            # The model is turned away for now. Say so in a form the run can act on: the pipeline
            # stops instead of walking every phase into the same wall, and the person is offered a
            # switch to another model (harness/model_block.py).
            from ..harness import model_block
            from ..platform.runtime import current_project_id

            error = model_block.ModelUnavailable(
                message,
                host=self.host_id,
                model=self.model,
                status=response.status_code,
                kind="no_credit" if response.status_code == 402 else "rate_limited",
            )
            model_block.report(current_project_id(), error)
            raise error
        raise RuntimeError(message)

    def _post(self, payload: dict[str, Any], *, stream: bool, abort: Callable[[], bool] | None = None) -> requests.Response:
        """POST to /chat/completions, retrying what is worth retrying.

        A rate limit (429) or a host hiccup (500/502/503/504/529, a dropped or timed-out connection)
        used to fail the whole turn -- or a whole SOP phase -- on the first try. Up to
        ``_RETRIES`` more attempts with exponential backoff and jitter, honouring ``Retry-After``.
        Nothing is retried once the reply has started (the caller only gets the response after
        its status line)."""
        abort = abort or (lambda: False)
        url = f"{self.api_root}/chat/completions"
        attempt = 0
        while True:
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=self._headers_for_call(),
                    timeout=(10, self.timeout) if stream else self.timeout,
                    stream=stream,
                )
            except requests.RequestException:
                if attempt >= _RETRIES or abort():
                    raise
                delay = _backoff(attempt)
            else:
                if response.status_code not in _RETRY_STATUS or attempt >= _retries_for(response, self.model) or abort():
                    return response
                delay = _retry_after(response) or _backoff(attempt)
                from ..services import usage_meter

                # Every retried attempt is a real request against the host's rate limit: meter it.
                usage_meter.record_error(self, response.status_code, _http_error_text(response))
                print(
                    f"[provider] {self.display_name} HTTP {response.status_code}; retrying in {delay:.1f}s",
                    flush=True,
                )
                response.close()
            attempt += 1
            if _sleep_unless(abort, delay):
                raise AgentAborted("stopped")

    def close_stream(self) -> None:
        """Stop: close every open stream now, so a reply that is still generating ends at once."""
        with self._streams_lock:
            streams = list(self._streams)
            self._streams.clear()
        for response in streams:
            response._catcrew_stopped = True  # type: ignore[attr-defined]
            _close_quietly(response)

    def _end_stream(self, response: requests.Response) -> None:
        with self._streams_lock:
            self._streams.discard(response)
        _close_quietly(response)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        self.last_usage = None
        self.last_finish = ""
        started = time.monotonic()
        try:
            response = self._post(self._payload(messages, tools, stream=False), stream=False)
        except requests.RequestException as exc:
            raise self._wrap_request(exc) from None
        response.encoding = "utf-8"
        if response.status_code >= 400:
            self._raise_http(response)
        body = response.json()
        self.last_finish = str(body["choices"][0].get("finish_reason") or "")
        message = body["choices"][0]["message"]
        content = message.get("content")
        if isinstance(content, str):
            from ..activity import repair_utf8_text

            content = repair_utf8_text(content)
            message["content"] = content
        calls = _parse_tools(message)
        usage = body.get("usage") if isinstance(body, dict) else None
        self.last_usage = usage if isinstance(usage, dict) else None
        _record(self, messages, content, calls, self.last_usage, time.monotonic() - started)
        return AssistantMessage(
            content=content,
            tool_calls=calls,
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
            response = self._post(self._payload(messages, tools, stream=True), stream=True, abort=abort)
        except requests.RequestException as exc:
            raise self._wrap_request(exc) from None
        with self._streams_lock:
            self._streams.add(response)
        if response.status_code == 400 and self.stream_usage and "stream_options" in _http_error_text(response).lower():
            # This host does not know the token-count option: stop asking (the meter estimates instead) and retry.
            self._end_stream(response)
            self.stream_usage = False
            return self.stream_chat(messages, tools, on_text, abort)
        if response.status_code >= 400:
            # Read the error body before closing: closing first threw it away, and the person saw
            # "HTTP 429: HTTP 429" instead of the host's reason.
            try:
                self._raise_http(response)
            finally:
                self._end_stream(response)

        from ..activity import repair_utf8_text

        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        # The model's reasoning, in the field the host used, to hand back with a tool round.
        thought_field = ""
        thought_parts: list[str] = []
        details: dict[int, dict[str, Any]] = {}
        # What the last call ended with, for an honest message when the model says nothing.
        self.last_finish = ""
        self.last_reasoning_chars = 0
        self.last_usage = None
        started = time.monotonic()
        first_at: float | None = None  # when the first token arrived: generation speed excludes the wait for it
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
                if isinstance(chunk.get("usage"), dict):
                    self.last_usage = chunk["usage"]  # the closing chunk of a host that reports token counts
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if choice.get("finish_reason"):
                    self.last_finish = str(choice["finish_reason"])
                thought = delta.get("reasoning_content") or delta.get("reasoning")
                if thought:
                    self.last_reasoning_chars += len(str(thought))
                    thought_field = thought_field or ("reasoning_content" if delta.get("reasoning_content") else "reasoning")
                    thought_parts.append(str(thought))
                _merge_reasoning_details(details, delta.get("reasoning_details"))
                piece = delta.get("content")
                if (piece or thought or delta.get("tool_calls")) and first_at is None:
                    first_at = time.monotonic()
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
        except (requests.RequestException, AttributeError, ValueError) as exc:
            # A stream closed under the reader (Stop) surfaces as whatever urllib3 trips over first.
            if abort() or getattr(response, "_catcrew_stopped", False):
                text = "".join(content_parts) or None
                return AssistantMessage(content=text, tool_calls=[], raw={})
            if not isinstance(exc, requests.RequestException):
                raise
            raise self._wrap_request(exc) from None
        finally:
            self._end_stream(response)

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
        finished = time.monotonic()
        _record(self, messages, text, tool_calls, self.last_usage, finished - (first_at if first_at is not None else started))
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
        if tool_calls:
            # Reasoning models (gpt-oss, DeepSeek, Claude, Gemini) continue a tool loop from their own
            # earlier reasoning; dropped, they lose the thread and often answer with nothing at all.
            # Hand it back the way the host sent it (OpenRouter: reasoning_details; DeepSeek:
            # reasoning_content), and only on tool rounds, where the host expects it.
            if details:
                raw["reasoning_details"] = [details[i] for i in sorted(details)]
            elif thought_parts and thought_field:
                raw[thought_field] = "".join(thought_parts)
        return AssistantMessage(content=text, tool_calls=tool_calls, raw=raw)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


class OpenRouterProvider(OpenAICompatProvider):
    """The one cloud provider: every vendor's models (DeepSeek, Claude, Gemini, Llama, ...) through
    OpenRouter's single API and key, chosen by model slug (``deepseek/deepseek-v4-pro``,
    ``anthropic/claude-sonnet-4``, ...). Prompt caching is on by default (``extra_body``/system-prompt
    ``cache_control``): a no-op on a model that does not support it, a real saving on one that does."""

    display_name = "OpenRouter"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: int = 180,
        extra_body: dict[str, Any] | None = None,
        enable_prompt_caching: bool | None = None,
    ) -> None:
        from ..config import OPENROUTER_PROMPT_CACHING

        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model or "openrouter/auto",
            timeout=timeout,
            extra_body=extra_body,
            headers={"HTTP-Referer": "https://cat-crew-ai.local", "X-Title": "CatCrewAI"},
            display_name="OpenRouter",
            host_id="openrouter",
            stream_usage=True,
            enable_prompt_caching=OPENROUTER_PROMPT_CACHING if enable_prompt_caching is None else enable_prompt_caching,
            error_hint="Check the OpenRouter key in Models → Hosts.",
        )
