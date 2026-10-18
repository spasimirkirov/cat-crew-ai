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
# gpt-oss "harmony" tool calls that a host passed through as text instead of parsing them:
# ``<|channel|>commentary to=functions.read_file <|constrain|>json<|message|>{"path": "a"}<|call|>``.
_HARMONY_CALL = re.compile(
    r"to=functions\.([A-Za-z0-9_]+)[^<]*(?:<\|constrain\|>[^<]*)?(?:<\|message\|>(.*?))?(?:<\|call\|>|<\|end\|>|$)",
    re.S,
)


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


def _flat_tool_args(item: dict[str, Any], nested: Any) -> dict[str, Any]:
    if isinstance(nested, dict) or (isinstance(nested, str) and nested.strip()):
        return _as_args(nested)
    skip = {"name", "function", "id", "type"}
    return {key: value for key, value in item.items() if key not in skip}


def _infer_delegate_call(item: dict[str, Any], allowed: set[str]) -> ToolCall | None:
    """Gemma Chief often prints {task, bot_id} with no function name."""
    bot_id = str(item.get("bot_id") or item.get("bot") or "").strip()
    task = str(item.get("task") or item.get("goal") or item.get("prompt") or "").strip()
    if not task:
        return None
    context = item.get("context") or item.get("extra_context")
    if bot_id and "delegate_to_bot" in allowed:
        tokens = _catalog_bot_tokens()
        if tokens is not None and bot_id.strip().lower() not in tokens:
            bot_id = ""
        if bot_id:
            args: dict[str, Any] = {"bot_id": bot_id, "task": task}
            if context:
                args["context"] = str(context)
            return ToolCall(name="delegate_to_bot", arguments=args)
    return None


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
        raw_args = item["arguments"] if "arguments" in item else fn.get("arguments")
        args = _flat_tool_args(item, raw_args)
        if name in allowed:
            calls.append(ToolCall(name=name, arguments=args))
            continue
        inferred = _infer_delegate_call({**item, **args}, allowed)
        if inferred is not None:
            calls.append(inferred)
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
    "restart_catcrew": ("reason",),
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


def _ast_call_from_args(raw_args: str) -> tuple[str, Any]:
    """Parse ``raw_args`` as a Python call's argument list.

    Returns ``(source, call_node)`` — ``source`` is the exact text that was
    actually fed to ``ast.parse`` (either ``raw_args`` itself, or the
    ``_soften_win_path_escapes``-repaired version when the raw text wasn't
    valid Python on its own, e.g. a trailing backslash right before the
    closing quote). Callers that need a string argument's *raw* source
    slice (see ``_decode_windows_ish_string_literal``) must use this
    ``source``, not ``raw_args``, since node offsets are relative to
    whichever string actually got parsed.
    """
    import ast

    try:
        source = f"f({raw_args})"
        return source, ast.parse(source, mode="eval").body
    except SyntaxError:
        softened = _soften_win_path_escapes(raw_args)
        source = f"f({softened})"
        return source, ast.parse(source, mode="eval").body


# Argument names that always hold a filesystem path (or a `cwd`) rather than
# file content, a regex, or anything else that legitimately wants Python's
# normal backslash-escape rules. Gemma (and similar local models without
# native function calling) print Windows paths as plain, unescaped text —
# e.g. ``D:\laragon\www\team-arcane\src\components\Contact.js`` — inside
# what looks like a Python string literal. Handing that straight to
# ``ast.literal_eval`` is wrong: ``\t``, ``\r``, ``\n``, ``\b``, ``\f`` are
# all *valid* Python escapes, so literal_eval silently swallows the leading
# letter of any segment named team-*, routes*, node_modules*, backend*, or
# frontend* into a control character, and every other ``\<letter>`` becomes
# a DeprecationWarning that still eats the backslash — the net effect on a
# real path being a completely flattened, separator-free filename (e.g. the
# ``\t`` of ``\team-arcane`` becomes a TAB character). Any
# string argument bound to one of these names is decoded from its raw
# source text instead (see ``_decode_windows_ish_string_literal``), so a
# literal backslash in the model's output survives as a literal backslash —
# which, on Windows, is exactly a valid path separator — instead of being
# reinterpreted as an escape sequence at all.
_PATH_LIKE_ARG_NAMES = frozenset({"path", "cwd"})


def _decode_windows_ish_string_literal(raw: str) -> str:
    """Decode a quoted Python string literal's *source text* without
    treating any backslash as a control-character escape.

    Only ``\\\\`` (an escaped backslash) and ``\\<quote>`` (an escaped quote,
    needed to keep the literal well-formed) are unescaped; every other
    ``\\X`` is kept as the two literal characters it already is. That is
    the only behavior that round-trips a Windows path correctly: the
    model's single backslashes survive as single backslashes, regardless of
    what letter follows one.
    """
    if len(raw) < 2 or raw[0] != raw[-1] or raw[0] not in "\"'":
        return raw  # not a plain quoted literal (e.g. a concatenation) — leave it alone
    quote = raw[0]
    body = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "\\" or nxt == quote:
                out.append(nxt)
            else:
                out.append(ch)
                out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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


def _eval_call_arg(source: str, key: str, arg_node: Any) -> Any:
    """Extract one argument's value from a parsed call.

    ``key in _PATH_LIKE_ARG_NAMES`` (a plain string literal) is decoded
    from its raw source slice via ``_decode_windows_ish_string_literal``
    instead of ``ast.literal_eval`` — see that function and
    ``_PATH_LIKE_ARG_NAMES`` for why: a real ``ast.literal_eval`` silently
    turns a Windows path's ``\\t``/``\\r``/``\\n``/``\\b``/``\\f`` segments
    into control characters. Everything else (file content, patterns,
    commands) keeps the normal Python literal evaluation unchanged.
    """
    import ast

    if (
        key in _PATH_LIKE_ARG_NAMES
        and isinstance(arg_node, ast.Constant)
        and isinstance(arg_node.value, str)
    ):
        raw = source[arg_node.col_offset : arg_node.end_col_offset]
        return _decode_windows_ish_string_literal(raw)
    return ast.literal_eval(arg_node)


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
                source, call = _ast_call_from_args(raw_args)
                if not isinstance(call, ast.Call):
                    continue
                keys = _PY_POSITIONAL.get(name, ())
                for index, arg_node in enumerate(call.args):
                    if index >= len(keys):
                        break
                    try:
                        args[keys[index]] = _eval_call_arg(source, keys[index], arg_node)
                    except (ValueError, TypeError):
                        args[keys[index]] = ""
                for kw in call.keywords:
                    if kw.arg is None:
                        continue
                    try:
                        args[kw.arg] = _eval_call_arg(source, kw.arg, kw.value)
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
    "restart": "restart_catcrew",
    "reboot": "restart_catcrew",
    "reload": "restart_catcrew",
    "restart_app": "restart_catcrew",
    "self_restart": "restart_catcrew",
    "restartcatcrew": "restart_catcrew",
}

# Safe to invoke with no arguments when printed alone on a line.
_BARE_INVOKE_OK: set[str] = {
    "restart_catcrew",
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


def _parse_cli_style_calls(text: str, allowed: set[str]) -> list[ToolCall]:
    """A ``tool_code`` marker line, then ``read_file path`` (no parentheses)."""
    calls: list[ToolCall] = []
    lines = (text or "").splitlines()
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


def _catalog_bot_tokens() -> set[str] | None:
    try:
        from ..platform.registry import load_registry

        bots = load_registry().bots
    except Exception:
        return None
    if not bots:
        return None
    tokens: set[str] = set()
    for bot in bots.values():
        tokens.add(bot.id.lower())
        tokens.add(bot.name.lower())
        first = bot.name.split()[0].lower() if bot.name else ""
        if first:
            tokens.add(first)
    return tokens


def extract_tool_calls_from_content(
    content: str | None,
    tools: list[dict[str, Any]],
) -> list[ToolCall]:
    """Tool calls a model printed as text instead of sending native ``tool_calls``.

    Every host CatCrewAI talks to is a cloud host with native function calling, so only explicit
    call syntax counts: a JSON tool-call object, a python-style call inside a code fence, a
    ``tool_code`` block, gpt-oss harmony and Qwen XML. The loose harvesters written for local
    models (a bare ``restart`` line, ``search_files "…"`` in prose, "I'm delegating to Pulse…")
    are gone: a model that quotes a fetched web page or a file would otherwise run whatever
    command-like line that text contains (prompt injection)."""
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
        if chunk is content:
            # A bare ``write_file("x", "y")`` in running prose may be quoted text: a python-style
            # call only counts inside a code fence.
            continue
        calls.extend(_parse_python_style_calls(chunk, allowed))

    # An explicit ``tool_code`` marker followed by one call line.
    calls.extend(_parse_cli_style_calls(content, allowed))

    if "to=functions." in content:
        for match in _HARMONY_CALL.finditer(content):
            name = match.group(1)
            if name in allowed:
                calls.append(ToolCall(name=name, arguments=_as_args((match.group(2) or "").strip())))

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
        r"(?:restart_catcrew|restart|reboot|reload|restart_app|self_restart|"
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
    #: The host this provider talks to ("openrouter", "groq", ...): what usage is metered under
    #: (``services/usage_meter``). Empty when unknown.
    host_id: str = ""

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
        **options: Any,
    ) -> str:
        """Run a whole agent turn with this provider: see ``harness/loop.run_agent``."""
        from ..harness.loop import run_agent

        return run_agent(self, messages, tools, execute_tool, **options)


_HISTORY_ARG_CAP = 600


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
