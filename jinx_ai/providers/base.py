"""Provider interface and function-calling agent loop."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

# Chat history message (OpenAI-style). Values may be nested
# (e.g. tool_calls), so we use Any.
Message = dict[str, Any]

# Runs a tool and returns a text result for the model.
ToolExecutor = Callable[[str, dict[str, Any]], str]
OnText = Callable[[str], None]
AbortFn = Callable[[], bool]
SteerFn = Callable[[], str | None]
OnTools = Callable[[list[str]], None]
SwapProvider = Callable[[], Any]


class AgentAborted(Exception):
    """User stopped the current agent turn."""

_FENCE_BLOCK = re.compile(r"```(?:json|tool_code|python|py)?\s*([\s\S]*?)```", re.I)
_QWEN_XML = re.compile(
    r"<tool_call>\s*([a-zA-Z0-9_]+)\s*(.*?)</tool_call>",
    re.S,
)
_QWEN_ARG = re.compile(
    r"<arg_key>\s*([^<]+?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
    re.S,
)
_PY_TOOL_CALL = re.compile(
    r"(?m)^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)\s*(?:#.*)?$",
    re.S,
)
_MAX_TEXT_TOOLS = 12


@dataclass
class ToolCall:
    """Normalized tool call, independent of the provider."""

    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass
class AssistantMessage:
    """Normalized assistant reply from one API call."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _allowed_tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for item in tools:
        fn = item.get("function") if isinstance(item, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            names.add(str(fn["name"]))
    return names


def _as_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _calls_from_obj(data: Any, allowed: set[str]) -> list[ToolCall]:
    if isinstance(data, dict) and isinstance(data.get("tool_calls"), list):
        return _calls_from_obj(data["tool_calls"], allowed)
    items = data if isinstance(data, list) else [data]
    calls: list[ToolCall] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(item.get("name") or fn.get("name") or "")
        args = _as_args(item.get("arguments", fn.get("arguments")))
        if name in allowed:
            calls.append(ToolCall(name=name, arguments=args))
    return calls


def _match_bracket(text: str, start: int) -> int:
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    if open_ch not in "{[":
        return -1
    depth = 0
    in_str = False
    esc = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _iter_json_blobs(text: str) -> list[str]:
    blobs: list[str] = []
    cursor = 0
    while cursor < len(text):
        brace = text.find("{", cursor)
        brack = text.find("[", cursor)
        if brace < 0 and brack < 0:
            break
        if brace < 0:
            start = brack
        elif brack < 0:
            start = brace
        else:
            start = min(brace, brack)
        end = _match_bracket(text, start)
        if end < 0:
            cursor = start + 1
            continue
        blobs.append(text[start:end])
        cursor = end
    return blobs


def _dedupe_calls(calls: list[ToolCall]) -> list[ToolCall]:
    out: list[ToolCall] = []
    seen: set[tuple[str, str]] = set()
    for call in calls:
        key = (
            call.name,
            json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(call)
    return out


# Positional arg order when Gemma prints write_file("p", "body") without kwargs.
_PY_POSITIONAL: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "content"),
    "replace_in_file": ("path", "old_string", "new_string"),
    "read_file": ("path",),
    "list_dir": ("path",),
    "run_command": ("command", "cwd"),
    "create_directory": ("path",),
    "glob_files": ("pattern", "path"),
    "search_files": ("query", "path"),
    "restart_jinx": ("reason",),
    "get_file_info": ("path",),
}


def _soften_win_path_escapes(src: str) -> str:
    """Only rewrite Windows path-looking escapes (``D:\\foo``), not PHP ``\\Support``."""
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\" and i + 1 < len(src):
                nxt = src[i + 1]
                if nxt in "\\\"'nrtbf":
                    out.append(ch)
                    out.append(nxt)
                    i += 2
                    continue
                # Drive path: D:\laragon\... — backslash before a path segment.
                prev = out[-1] if out else ""
                if prev == ":" or (prev in "/\\" and nxt.isalpha()):
                    out.append("/")
                    out.append(nxt)
                    i += 2
                    continue
                # Keep unknown escapes doubled so ast.parse accepts them as literals.
                out.append("\\\\")
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
        out.append(ch)
        i += 1
    return "".join(out)


def _ast_call_from_args(raw_args: str):
    import ast

    try:
        return ast.parse(f"f({raw_args})", mode="eval").body
    except SyntaxError:
        softened = _soften_win_path_escapes(raw_args)
        return ast.parse(f"f({softened})", mode="eval").body


def _split_python_call_pieces(text: str) -> list[str]:
    """Split a fence body into top-level call expressions (paren-aware, string-safe)."""
    pieces: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    escape = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for ch in stripped:
            if quote:
                buf.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                buf.append(ch)
                continue
            if ch == "(":
                depth += 1
                buf.append(ch)
                continue
            if ch == ")":
                depth -= 1
                buf.append(ch)
                continue
            buf.append(ch)
        if depth <= 0 and buf:
            piece = "".join(buf).strip()
            if piece:
                pieces.append(piece)
            buf = []
            depth = 0
        elif buf:
            buf.append(" ")
    if buf:
        piece = "".join(buf).strip()
        if piece:
            pieces.append(piece)
    return pieces


def _parse_python_style_calls(text: str, allowed: set[str]) -> list[ToolCall]:
    """Gemma often prints ``run_command(command=\"…\", cwd=\".\")`` inside fences."""
    import ast

    calls: list[ToolCall] = []
    for piece in _split_python_call_pieces(text):
        match = re.match(
            r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)\s*$",
            piece,
            re.S,
        )
        if not match:
            continue
        name = _resolve_tool_name(match.group(1), allowed)
        if not name:
            continue
        raw_args = match.group(2).strip()
        args: dict[str, Any] = {}
        if raw_args:
            try:
                call = _ast_call_from_args(raw_args)
                if not isinstance(call, ast.Call):
                    continue
                keys = _PY_POSITIONAL.get(name, ())
                for index, arg_node in enumerate(call.args):
                    if index >= len(keys):
                        break
                    try:
                        args[keys[index]] = ast.literal_eval(arg_node)
                    except (ValueError, TypeError):
                        args[keys[index]] = ""
                for kw in call.keywords:
                    if kw.arg is None:
                        continue
                    try:
                        args[kw.arg] = ast.literal_eval(kw.value)
                    except (ValueError, TypeError):
                        args[kw.arg] = ""
                # Map common Gemma typos to real schema fields.
                if "pattern" in args and "old_string" not in args:
                    args["old_string"] = args.pop("pattern")
                if "replacement" in args and "new_string" not in args:
                    args["new_string"] = args.pop("replacement")
            except SyntaxError:
                continue
        calls.append(ToolCall(name=name, arguments=args))
    return calls


_TOOL_CODE_MARK = re.compile(
    r"(?im)^\s*(?:```)?\s*tool_code\s*(?:```)?\s*$"
)

# Gemma often prints a short alias instead of the real tool name.
_TOOL_ALIASES: dict[str, str] = {
    "restart": "restart_jinx",
    "reboot": "restart_jinx",
    "reload": "restart_jinx",
    "restart_app": "restart_jinx",
    "self_restart": "restart_jinx",
    "restartjinx": "restart_jinx",
}

# Safe to invoke with no arguments when printed alone on a line.
_BARE_INVOKE_OK: set[str] = {
    "restart_jinx",
    "git_status",
    "git_pull",
    "git_push",
    "list_allowed_directories",
    "list_connectors",
    "db_list_connections",
    "system_time",
    "hardware_info",
    "docker_info",
    "comfy_info",
    "comfy_list",
    "comfy_interrupt",
    "comfy_stop",
}


def _resolve_tool_name(name: str, allowed: set[str]) -> str | None:
    key = (name or "").strip()
    if not key:
        return None
    if key in allowed:
        return key
    mapped = _TOOL_ALIASES.get(key.lower())
    if mapped and mapped in allowed:
        return mapped
    lower = {item.lower(): item for item in allowed}
    if key.lower() in lower:
        return lower[key.lower()]
    if mapped and mapped.lower() in lower:
        return lower[mapped.lower()]
    return None


def _cli_line_to_call(line: str, allowed: set[str]) -> ToolCall | None:
    """Parse ``read_file path`` / ``restart`` / ``replace_in_file …``."""
    import shlex

    raw = (line or "").strip().strip("`")
    if not raw or raw.startswith("#"):
        return None
    if raw.lower().startswith("tool_code "):
        raw = raw[10:].strip()
    match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\b(.*)$", raw, re.S)
    if not match:
        return None
    name = _resolve_tool_name(match.group(1), allowed)
    if not name:
        return None
    rest = match.group(2).strip()
    if rest.startswith("("):
        return None

    if name == "run_command":
        command = rest.strip().strip("\"'")
        if not command:
            return None
        return ToolCall(name=name, arguments={"command": command})

    keys = _PY_POSITIONAL.get(name, ())
    if not rest:
        if name in _BARE_INVOKE_OK or not keys:
            return ToolCall(name=name, arguments={})
        return None
    try:
        parts = shlex.split(rest, posix=True)
    except ValueError:
        parts = rest.split()
    if not parts:
        if name in _BARE_INVOKE_OK or not keys:
            return ToolCall(name=name, arguments={})
        return None
    cleaned: list[str] = []
    for index, part in enumerate(parts):
        if (
            index > 0
            and part.startswith("-")
            and not part.startswith("--")
            and len(part) > 1
            and part[1:].replace("_", "").isalnum()
        ):
            cleaned.append(part[1:])
        else:
            cleaned.append(part)
    args: dict[str, Any] = {}
    for index, key in enumerate(keys):
        if index < len(cleaned):
            args[key] = cleaned[index]
    if name == "search_files" and "path" in args:
        args["path"] = str(args["path"]).lstrip("-")
    if "pattern" in args and "old_string" not in args:
        args["old_string"] = args.pop("pattern")
    if "replacement" in args and "new_string" not in args:
        args["new_string"] = args.pop("replacement")
    if name in {"read_file", "write_file", "replace_in_file", "get_file_info"}:
        path_arg = str(args.get("path") or "")
        if path_arg and "/" not in path_arg and "\\" not in path_arg and "." not in path_arg:
            return None
    return ToolCall(name=name, arguments=args)


def _parse_cli_style_calls(
    text: str,
    allowed: set[str],
    *,
    require_marker: bool = True,
) -> list[ToolCall]:
    """Gemma prints bare ``tool_code`` then ``read_file path`` (no parentheses)."""
    calls: list[ToolCall] = []
    lines = (text or "").splitlines()
    if not require_marker:
        for line in lines:
            call = _cli_line_to_call(line, allowed)
            if call is not None:
                calls.append(call)
        return calls

    i = 0
    while i < len(lines):
        if _TOOL_CODE_MARK.match(lines[i]):
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i >= len(lines):
                break
            piece = lines[i].strip()
            if piece in {"```", "```tool_code"}:
                i += 1
                continue
            call = _cli_line_to_call(piece, allowed)
            if call is not None:
                calls.append(call)
        i += 1
    return calls


def _harvest_action_lines(text: str, allowed: set[str]) -> list[ToolCall]:
    """Pick up tool lines Gemma drops into chat (``restart``, ``search_files …``)."""
    calls: list[ToolCall] = []
    names = sorted(set(allowed) | set(_TOOL_ALIASES), key=len, reverse=True)
    if not names:
        return calls
    keys = "|".join(re.escape(name) for name in names)
    pattern = re.compile(rf"^(?:tool_code\s+)?(?:{keys})\b", re.I)
    for line in (text or "").splitlines():
        stripped = line.strip().strip("`")
        if not pattern.match(stripped):
            continue
        call = _cli_line_to_call(stripped, allowed)
        if call is not None:
            calls.append(call)
    return calls



def extract_tool_calls_from_content(
    content: str | None,
    tools: list[dict[str, Any]],
) -> list[ToolCall]:
    """Qwen/Gemma often print tool JSON or python-style calls instead of native tool_calls."""
    if not content:
        return []
    allowed = _allowed_tool_names(tools)
    if not allowed:
        return []

    calls: list[ToolCall] = []
    fence_bodies = [match.group(1) for match in _FENCE_BLOCK.finditer(content)]
    chunks = [content, *fence_bodies]
    seen_blobs: set[str] = set()
    for chunk in chunks:
        for blob in _iter_json_blobs(chunk):
            if blob in seen_blobs:
                continue
            seen_blobs.add(blob)
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            calls.extend(_calls_from_obj(data, allowed))
        calls.extend(_parse_python_style_calls(chunk, allowed))

    # Bare tool_code blocks in the full reply (Gemma's favorite broken form).
    calls.extend(_parse_cli_style_calls(content, allowed, require_marker=True))
    # Inside fences, lines are already the tool body — no marker required.
    for body in fence_bodies:
        calls.extend(_parse_cli_style_calls(body, allowed, require_marker=False))
    # Harvest lines like ``restart`` / ``search_files "…"`` dropped into chat.
    calls.extend(_harvest_action_lines(content, allowed))

    for match in _QWEN_XML.finditer(content):
        name = match.group(1).strip()
        body = match.group(2)
        inner = body.strip()
        if inner.startswith("{") or inner.startswith("```"):
            calls.extend(extract_tool_calls_from_content(inner, tools))
            continue
        args: dict[str, Any] = {}
        for key, val in _QWEN_ARG.findall(body):
            args[key.strip()] = val.strip()
        if name in allowed:
            calls.append(ToolCall(name=name, arguments=args))
    return _dedupe_calls(calls)[:_MAX_TEXT_TOOLS]


def _looks_like_printed_tool(data: Any) -> bool:
    if isinstance(data, dict) and isinstance(data.get("tool_calls"), list):
        return True
    items = data if isinstance(data, list) else [data]
    if not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = item.get("name") or fn.get("name")
        if not name:
            return False
        if "arguments" not in item and "arguments" not in fn:
            return False
    return True


def strip_printed_tool_json(text: str) -> tuple[str, int]:
    """Remove tool-call JSON the model printed as chat. Returns (text, count)."""
    if not text:
        return "", 0
    keep = text
    removed = 0
    for blob in _iter_json_blobs(text):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not _looks_like_printed_tool(data):
            continue
        keep = keep.replace(blob, "\n", 1)
        removed += 1
    # Bare Gemma ``tool_code`` / fenced tool_code blocks (not real function calls).
    bare = re.sub(
        r"(?im)^\s*(?:```)?\s*tool_code\s*(?:```)?\s*\n(?:[^\n]*\n)?",
        "\n",
        keep,
    )
    if bare != keep:
        removed += 1
        keep = bare
    # Lone printed tool names / aliases (executed via text extract, not chat).
    lone = re.sub(
        r"(?im)^\s*(?:tool_code\s+)?"
        r"(?:restart_jinx|restart|reboot|reload|restart_app|self_restart|"
        r"git_status|git_pull|git_push|list_allowed_directories|list_connectors|"
        r"system_time|hardware_info|docker_info|comfy_info|comfy_list|"
        r"comfy_interrupt|comfy_stop)\s*$",
        "",
        keep,
    )
    if lone != keep:
        removed += 1
        keep = lone
    keep = re.sub(r"```(?:json|tool_code|python|py)?\s*```", "\n", keep, flags=re.I)
    keep = _QWEN_XML.sub("\n", keep)
    return keep, removed


class BaseProvider(ABC):
    """Common interface every provider must implement."""

    display_name: str = "Base Provider"

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        """Send messages and return a normalized assistant reply."""
        raise NotImplementedError

    @abstractmethod
    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        """Return a tool-result message in this provider's format."""
        raise NotImplementedError

    def assistant_tool_turn(self, reply: AssistantMessage) -> Message:
        """Assistant+tools message when the model wrote tools as text."""
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            _slim_args_for_history(call.arguments),
                            ensure_ascii=False,
                        ),
                    },
                }
                for index, call in enumerate(reply.tool_calls)
            ],
        }

    def close_stream(self) -> None:
        """Abort an in-flight HTTP stream, if the provider opened one."""

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        on_text: OnText | None = None,
        abort: AbortFn | None = None,
    ) -> AssistantMessage:
        """Default: one-shot chat. Providers may stream content deltas."""
        if abort and abort():
            raise AgentAborted("stopped")
        reply = self.chat(messages, tools)
        if on_text and reply.content and not reply.tool_calls:
            on_text(reply.content)
        return reply

    def run_agent(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_rounds: int = 0,
        on_text: OnText | None = None,
        abort: AbortFn | None = None,
        take_steer: SteerFn | None = None,
        on_tools: OnTools | None = None,
        swap_provider: SwapProvider | None = None,
    ) -> str:
        """Full conversation loop with function calling.

        Call the model, run requested tools, return the final text.
        ``max_rounds <= 0`` means unlimited (Stop / abort ends the turn).
        """
        abort = abort or (lambda: False)
        current: BaseProvider = self
        reply = AssistantMessage(content="")
        last_text = ""
        seen_calls: dict[tuple[str, str], str] = {}
        skip_streak = 0
        explore_n = 0
        explore_steered = False
        try:
            cap = int(max_rounds or 0)
            if cap < 0:
                cap = 0
            round_i = 0
            while True:
                if cap and round_i >= cap:
                    break
                round_i += 1
                if abort():
                    raise AgentAborted("stopped")
                if swap_provider is not None:
                    nxt = swap_provider()
                    if nxt is not None:
                        current.close_stream()
                        current = nxt

                held: list[str] = []

                def _on_delta(piece: str) -> None:
                    held.append(piece)

                def _flush_held() -> None:
                    if on_text and held:
                        on_text("".join(held))

                from ..harness.deferred import extra_tool_schemas

                round_tools = list(tools) + extra_tool_schemas()
                reply = current.stream_chat(
                    messages, round_tools, on_text=_on_delta, abort=abort
                )
                last_text = (reply.content or "").strip()
                if abort():
                    _flush_held()
                    return _with_auto_restart(last_text or "[stopped]")

                from_text = False
                if not reply.tool_calls:
                    reply.tool_calls = extract_tool_calls_from_content(
                        reply.content, round_tools
                    )
                    if reply.tool_calls:
                        from_text = True
                        print(
                            f"[agent] tool call from text: "
                            f"{[call.name for call in reply.tool_calls]}",
                            flush=True,
                        )
                    else:
                        _flush_held()
                        return _with_auto_restart(last_text)

                for index, call in enumerate(reply.tool_calls):
                    if not call.id:
                        call.id = f"call_{index}"

                # Native OpenAI tool_calls: keep role=tool protocol.
                # Printed tool_code (Gemma/Qwen): DO NOT inject role=tool —
                # llama.cpp Jinja for those models expects user/assistant
                # alternation and returns HTTP 400 on synthetic tool turns.
                if not from_text:
                    messages.append(reply.raw or current.assistant_tool_turn(reply))

                reply.tool_calls = _dedupe_calls(reply.tool_calls)
                last_text = ""
                fresh = [call for call in reply.tool_calls if _call_key(call) not in seen_calls]
                if on_tools and fresh:
                    on_tools([call.name for call in fresh])

                ran_new = False
                text_pairs: list[tuple[ToolCall, str]] = []
                batch_failed = False
                # Never restart in the same breath as unverified edits: run
                # restart_jinx last, and skip it if earlier tools in this batch failed.
                ordered_calls = [
                    call for call in reply.tool_calls if call.name != "restart_jinx"
                ] + [
                    call for call in reply.tool_calls if call.name == "restart_jinx"
                ]
                for call in ordered_calls:
                    if abort():
                        raise AgentAborted("stopped")
                    key = _call_key(call)
                    previous = seen_calls.get(key)
                    if previous is not None:
                        result = _repeat_skip_text(call, previous)
                        from ..harness.events import emit_tool_result, tool_info_for_name

                        emit_tool_result(
                            call.name,
                            result,
                            skipped=True,
                            arguments=call.arguments,
                            tool_info=tool_info_for_name(call.name),
                        )
                    elif call.name == "restart_jinx" and batch_failed:
                        result = (
                            "Skipped restart_jinx: earlier tools in this batch failed. "
                            "Read the errors, fix the files, then call restart_jinx alone."
                        )
                        from ..harness.events import emit_tool_result, tool_info_for_name

                        emit_tool_result(
                            call.name,
                            result,
                            skipped=True,
                            arguments=call.arguments,
                            tool_info=tool_info_for_name(call.name),
                        )
                        seen_calls[key] = result
                    else:
                        from ..harness.events import (
                            emit_tool_call,
                            emit_tool_result,
                            tool_info_for_name,
                        )

                        info = tool_info_for_name(call.name)
                        started = emit_tool_call(
                            call.name,
                            call.arguments,
                            tool_info=info,
                        )
                        from ..harness.approvals import gate_tool
                        from ..harness.offload import maybe_offload_tool_result

                        denied = gate_tool(call.name, call.arguments)
                        if denied:
                            result = denied
                        else:
                            result = execute_tool(call.name, call.arguments)
                        result = maybe_offload_tool_result(call.name, result)
                        seen_calls[key] = result
                        ran_new = True
                        emit_tool_result(
                            call.name,
                            result,
                            call_id=str(started.payload.get("call_id") or ""),
                            arguments=call.arguments,
                            tool_info=info,
                        )
                    if _failed_tool_result(result) and call.name != "restart_jinx":
                        batch_failed = True
                    if from_text:
                        text_pairs.append((call, result))
                    else:
                        messages.append(current.tool_message(call, result))
                    if take_steer:
                        steered = take_steer()
                        if steered:
                            messages.append({"role": "user", "content": steered})

                if from_text and text_pairs:
                    prose = _strip_printed_tools(reply.content) or "Working."
                    messages.append({"role": "assistant", "content": prose})
                    messages.append(
                        {"role": "user", "content": _text_tool_results_user(text_pairs)}
                    )
                    steer = _replace_fail_steer_from_pairs(text_pairs)
                    if steer:
                        messages.append({"role": "user", "content": steer})
                elif batch_failed and any(
                    call.name == "replace_in_file" for call in ordered_calls
                ):
                    # Prefer the real tool error; only nudge when it named wrong paths.
                    for call in ordered_calls:
                        if call.name != "replace_in_file":
                            continue
                        prev = seen_calls.get(_call_key(call), "")
                        steer = _replace_fail_steer(prev)
                        if steer:
                            messages.append({"role": "user", "content": steer})
                            break

                explore_n += sum(
                    1 for call in ordered_calls if call.name in _EXPLORE_TOOLS
                )
                if not explore_steered and explore_n >= 6:
                    explore_steered = True
                    messages.append({"role": "user", "content": _EXPLORE_STEER})

                if reply.tool_calls and not ran_new:
                    skipped_names = {call.name for call in reply.tool_calls}
                    if skipped_names & _DELEGATE_TOOLS:
                        messages.append({"role": "user", "content": _REDELEGATE_STEER})
                        skip_streak = 0
                    else:
                        skip_streak += 1
                        if skip_streak >= 2:
                            return _with_auto_restart(
                                "I got stuck repeating the same failed tool. "
                                "Send continue and I will try a different approach "
                                "for your latest request."
                            )
                        messages.append({"role": "user", "content": _REPEAT_STEER})
                else:
                    skip_streak = 0
        except AgentAborted:
            current.close_stream()
            return _with_auto_restart(last_text or "[stopped]")

        return _with_auto_restart(
            last_text
            or (
                f"Stopped after {cap or round_i} tool rounds with work still in flight. "
                "Continue the same task — do not ask the human Continue or Stop."
            )
        )


def _with_auto_restart(text: str) -> str:
    """If Jinx source was patched this turn and she forgot restart_jinx, do it."""
    try:
        from ..pending_restart import maybe_auto_restart_after_edits

        auto = maybe_auto_restart_after_edits()
    except Exception:  # noqa: BLE001
        return text
    if not auto:
        return text
    base = (text or "").strip()
    if not base:
        return auto
    return f"{base}\n\n{auto}"


_HISTORY_ARG_CAP = 600


def _strip_printed_tools(content: str | None) -> str:
    """Keep spoken/prose bits; drop tool_code fences so they are not re-sent."""
    text = _FENCE_BLOCK.sub("", content or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:2_000]


def _text_tool_results_user(pairs: list[tuple[ToolCall, str]]) -> str:
    blocks: list[str] = []
    for call, result in pairs:
        slim = _slim_args_for_history(call.arguments)
        blocks.append(
            f"### {call.name}\n"
            f"args: {json.dumps(slim, ensure_ascii=False)}\n"
            f"result:\n{result}"
        )
    return (
        "Tool results from your last printed tool_code / JSON calls "
        "(already executed — do not repeat the same call):\n\n"
        + "\n\n".join(blocks)
        + "\n\nContinue the task with more tools if needed. "
        "Do not claim the project is done until tool results confirm it."
    )


def _slim_args_for_history(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Keep tool execution args intact; shrink huge strings in chat history.

    Gemma often prints full ``write_file`` bodies as text tools. Replaying those
    verbatim on the next round blows local context and yields HTTP 500.
    """
    out: dict[str, Any] = {}
    for key, value in dict(arguments or {}).items():
        if isinstance(value, str) and len(value) > _HISTORY_ARG_CAP:
            out[key] = (
                f"{value[:200]}\n… [{len(value)} chars total — applied; "
                "do not reprint full body]"
            )
        else:
            out[key] = value
    return out


def _call_key(call: ToolCall) -> tuple[str, str]:
    args = dict(call.arguments or {})
    for field in ("path", "cwd", "src", "dest"):
        value = args.get(field)
        if isinstance(value, str) and value.strip():
            args[field] = value.replace("/", "\\").rstrip("\\").lower()
    return (
        call.name,
        json.dumps(args, sort_keys=True, ensure_ascii=False),
    )


def _failed_tool_result(result: str) -> bool:
    text = result or ""
    return (
        text.startswith("Error:")
        or text.startswith("Refused:")
        or text.startswith("cd does not persist")
    )


def _repeat_skip_text(call: ToolCall, previous: str) -> str:
    if _failed_tool_result(previous):
        return (
            f"Skipped: {call.name} already failed this turn with these arguments. "
            "Do NOT retry the same call. Read the earlier Error, then use a different "
            "path, different old_string, or a different tool (search_files / read_file)."
        )
    return (
        f"Skipped: {call.name} already ran with these arguments this turn. "
        "Use the earlier tool result. Next must be a different tool "
        "(read_file, write_file, replace_in_file, or run_command) — "
        "not the same list_dir/glob again."
    )


def _replace_fail_steer(result: str) -> str | None:
    """Nudge only when the tool already named the correct paths (title wrong-file)."""
    text = result or ""
    if not _failed_tool_result(text):
        return None
    low = text.lower()
    if "wrong file" in low or "titlebar.py" in low or "window / title-bar" in low:
        return (
            "Follow the Error paths exactly (usually titlebar.py + config.py for the "
            "window title). Call replace_in_file on those files next — do not retry "
            "widgets.py / ui_style.py. Do not print tools in chat."
        )
    return (
        "replace_in_file failed. Use the Error text: read the file or search_files "
        "for a unique fragment, then patch with the exact old_string. "
        "Do not retry the same arguments."
    )


def _replace_fail_steer_from_pairs(pairs: list[tuple[ToolCall, str]]) -> str | None:
    for call, result in pairs:
        if call.name == "replace_in_file" and _failed_tool_result(result):
            return _replace_fail_steer(result)
    return None


_REPEAT_STEER = (
    "Stop repeating that tool — you already have the result in this turn. "
    "Change approach: different path, different arguments, or a different tool. "
    "Answer the user's latest message; do not revive an older finished task."
)
_EXPLORE_TOOLS = frozenset(
    {"search_files", "web_search", "web_fetch", "glob_files", "list_dir"}
)
_EXPLORE_STEER = (
    "Stop exploring. If the computer folder is empty, scaffold now with "
    "run_command (npm create vite@latest . -- --template react) or write_file. "
    "Do not search_files or web_fetch again until files exist."
)
_DELEGATE_TOOLS = frozenset({"delegate_to_bot", "delegate_to_team", "run_subagent"})
_REDELEGATE_STEER = (
    "That exact delegate already ran this turn. Call delegate_to_bot with a "
    "DIFFERENT bot_id (heimerdinger, jinx, ekko, caitlyn). "
    "The team lead cannot write files. Then list_dir to verify package.json."
)
