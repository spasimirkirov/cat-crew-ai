"""Headless Jinx CLI — one-shot and REPL, like using Hermes in the terminal.

Wires Jinx's own agent loop (provider.run_agent + execute_tool) without PyQt.
Two modes:

    jinx "task"        one-shot, print reply + tool steps
    jinx repl          interactive session, keeps chat history in memory

Config (in order of precedence):
    JINX_CLI_HOME       data/ folder root (default C:\\Jinx-AI)
    JINX_CLI_SESSION    session id + trace + todo board name
    JINX_CLI_MODEL      GGUF stem or cloud model to use for chat
    JINX_CLI_HOST       local | deepseek | gemini | groq
    JINX_CLI_WORKSPACE  attached folder(s), comma-separated

This process has its own lock (data/jinx-cli.lock) so it can run alongside the
GUI without fighting single_instance.py. It uses the app's Python and reads the
same settings/keys the GUI would.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the app package importable when invoked as a script from anywhere under the repo.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from jinx_ai.config import BASE_DIR
from jinx_ai.harness.approvals import set_approval_hook
from jinx_ai.harness.ask_user import set_ask_user_hook
from jinx_ai.harness.deferred import clear_loaded_tools
from jinx_ai.harness.runtime import set_abort, set_provider
from jinx_ai.settings_store import agent_settings, resolve_chat_brain, save_agent_settings
from jinx_ai.tools import build_system_prompt, execute_tool, tools_schema_for
from jinx_ai.trace import bind_session, write_markdown
from jinx_ai.workspace import set_folders
from jinx_ai.api_access import make_cloud_provider
from jinx_ai.providers import LocalLlamaProvider

# Default CLI data lives under the app tree, separate from the GUI session store.
CLI_DATA = BASE_DIR / "data" / "cli"
CLI_DATA.mkdir(parents=True, exist_ok=True)


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _session_id() -> str:
    raw = _env_str("JINX_CLI_SESSION", "cli-repl")
    return raw or "cli-repl"


def _host() -> str:
    raw = _env_str("JINX_CLI_HOST", "").lower()
    if raw in {"local", "deepseek", "gemini", "groq"}:
        return raw
    return "local"


def _model() -> str:
    return _env_str("JINX_CLI_MODEL", "")


def _workspaces() -> list[Path]:
    raw = _env_str("JINX_CLI_WORKSPACE", "").replace(";", ",")
    out: list[Path] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            p = Path(part).expanduser().resolve()
        except Exception:
            continue
        if p.is_dir():
            out.append(p)
    return out


def _default_command_timeout() -> int:
    try:
        return int(agent_settings().get("command_timeout") or 60)
    except Exception:
        return 60


def _default_max_rounds() -> int:
    try:
        return int(agent_settings().get("max_rounds") or 0)
    except Exception:
        return 0


def _cli_lock_path() -> Path:
    return CLI_DATA / "jinx-cli.lock"


def _acquire_cli() -> bool:
    """Best-effort single-process guard for the CLI data folder.

    Not as strict as the GUI's QLockFile on the whole app data dir — we only
    guard the CLI subfolder so the GUI can still run.
    """
    from filelock import FileLock

    lock = FileLock(str(_cli_lock_path()), timeout=1.5)
    try:
        with lock:
            return True
    except Exception:
        return False


def _build_provider(host: str, model: str):
    session = {}
    if model:
        session["model"] = model
    h, m = resolve_chat_brain(session={"provider": host} if host else None)
    effective_host = host or h
    effective_model = model or m
    if effective_host in {"deepseek", "gemini", "groq"}:
        return make_cloud_provider(effective_host, effective_model)
    return LocalLlamaProvider(model=effective_model)


def _print_tool_step(name: str, arguments: dict, result: str) -> None:
    args_txt = ""
    try:
        import json as _json

        args_txt = _json.dumps(arguments, ensure_ascii=False)
        if len(args_txt) > 240:
            args_txt = args_txt[:240] + "..."
    except Exception:
        args_txt = str(arguments)
    print(f"[{_host().upper()}] {name}({args_txt})", flush=True)
    preview = result.replace("\n", " ")
    if len(preview) > 220:
        preview = preview[:220] + "..."
    print(f"[{_host().upper()}] -> {preview}", flush=True)


def _prompt_loop(history: list[dict], provider, tools, timeout: int, rounds: int):
    """Interactive REPL. Keeps the same session + todo board across turns."""
    try:
        while True:
            try:
                text = input("jinx> ").strip()
            except EOFError:
                print()
                break
            if not text:
                continue
            if text in {"quit", "exit", "q"}:
                break
            if text in {"clear", "reset"}:
                history.clear()
                print("[cli] session cleared.")
                continue

            turn_messages: list[dict] = [
                {"role": "system", "content": build_system_prompt(mode="developer")},
                *history,
                {"role": "user", "content": text},
            ]

            def on_tools(names: list[str]) -> None:
                print(f"[{_host().upper()}] tools: {', '.join(names)}", flush=True)

            try:
                reply = provider.run_agent(
                    turn_messages,
                    tools,
                    execute_tool,
                    max_rounds=rounds,
                    on_tools=on_tools,
                )
            except Exception as exc:  # noqa: BLE001
                reply = f"[cli error] {exc}"

            print(f"\n{reply}\n")
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply or ""})
    except KeyboardInterrupt:
        print()
    finally:
        write_markdown(_session_id())


def _one_shot(
    text: str,
    provider,
    tools,
    timeout: int,
    rounds: int,
    *,
    project_id: str = "",
    bot_id: str = "",
) -> str:
    host_label = _host()
    print(f"[{host_label}] task: {text}", flush=True)
    print(f"[{host_label}] model: {host_label}/{provider.model or _model() or '(auto)'}", flush=True)

    system = build_system_prompt(
        mode="developer",
        project_id=project_id,
        bot_id=bot_id,
    )
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]

    def on_tools(names: list[str]) -> None:
        print(f"[{host_label}] tools: {', '.join(names)}", flush=True)

    try:
        reply = provider.run_agent(
            messages,
            tools,
            execute_tool,
            max_rounds=rounds,
            on_tools=on_tools,
        )
    except Exception as exc:  # noqa: BLE001
        reply = f"[cli error] {exc}"

    print(f"\n{reply}\n", flush=True)
    write_markdown(_session_id())
    return reply or ""


@dataclass
class CliConfig:
    host: str
    model: str
    workspace: list[Path]
    timeout: int
    rounds: int
    session_id: str
    project_id: str = ""
    bot_id: str = "chief"


def configure_cli(cfg: CliConfig) -> None:
    # Bind the CLI session so traces and the harness TODO board live under it.
    bind_session(cfg.session_id)
    clear_loaded_tools()
    folders = list(cfg.workspace)
    if cfg.project_id:
        from .platform.runtime import set_bot_id, set_project_id
        from .sessions import open_project_thread
        from .workspace import current_folders

        session = open_project_thread(
            cfg.project_id,
            cfg.bot_id or "chief",
            title=cfg.session_id,
        )
        bind_session(str(session.get("id") or cfg.session_id))
        set_project_id(cfg.project_id)
        set_bot_id(cfg.bot_id or "chief")
        live = current_folders()
        if live:
            folders = live
    set_folders(folders)

    # Approvals/ask_user: in the CLI we default-approve unless the user opts in
    # via Settings → General → tool_approval (read from the shared settings.json).
    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if agent_settings().get("tool_approval") and interactive:
        def _approval(name: str, arguments: dict) -> str:
            answer = input(f" approve {name}? (allow/deny/always) ").strip().lower()
            if answer in {"allow", "always", "yes"}:
                return "allow" if answer != "always" else "allow_always"
            return "deny"
        set_approval_hook(_approval)
    else:
        set_approval_hook(lambda _n, _a: "allow")

    def _ask(prompt: str, choices: list[str]) -> str:
        if not interactive:
            return (choices[0] if choices else "Keep working. You decide.")
        if choices:
            print(f"ask_user: {prompt}")
            for i, c in enumerate(choices, 1):
                print(f"  {i}. {c}")
            try:
                pick = input(" choice (number or text): ").strip()
            except EOFError:
                return choices[0]
            if pick.isdigit() and 1 <= int(pick) <= len(choices):
                return choices[int(pick) - 1]
            return pick or choices[0]
        try:
            return input(f"ask_user: {prompt} ").strip()
        except EOFError:
            return "Keep working. You decide."

    def _abort() -> bool:
        return _cli_abort_flag.is_set()

    set_ask_user_hook(_ask)
    set_abort(_abort)
    set_provider(_build_provider(cfg.host, cfg.model))


# Shared abort flag for Ctrl+C in one-shot mode.
_cli_abort_flag = threading.Event()


def main() -> int:
    parser = argparse.ArgumentParser(prog="jinx", description="Jinx AI headless CLI")
    mode = parser.add_subparsers(dest="mode")

    one = mode.add_parser("run", help="one-shot task")
    one.add_argument("task", nargs="+", help="task text (quoted)")
    one.add_argument("--timeout", type=int, default=None)
    one.add_argument("--rounds", type=int, default=None)
    one.add_argument("--project", default="", help="project id (Everyone / Chief tools)")
    one.add_argument("--bot", default="chief", help="bot id (chief = Everyone)")

    rep = mode.add_parser("repl", help="interactive session")
    rep.add_argument("--timeout", type=int, default=None)
    rep.add_argument("--rounds", type=int, default=None)
    rep.add_argument("--project", default="", help="project id (Everyone / Chief tools)")
    rep.add_argument("--bot", default="chief", help="bot id (chief = Everyone)")

    args = parser.parse_args()

    cfg = CliConfig(
        host=_host(),
        model=_model(),
        workspace=_workspaces(),
        timeout=_default_command_timeout() if args.timeout is None else args.timeout,
        rounds=_default_max_rounds() if args.rounds is None else args.rounds,
        session_id=_session_id(),
        project_id=str(getattr(args, "project", "") or ""),
        bot_id=str(getattr(args, "bot", "") or "chief"),
    )

    if cfg.workspace:
        print(f"[cli] workspace: {', '.join(str(p) for p in cfg.workspace)}")
    else:
        print("[cli] workspace: (none — tasks run in CLI scratch)")
    print(f"[cli] session: {cfg.session_id}")
    print(f"[cli] host: {_host()}  model: {_model() or '(from settings)'}")
    rounds_label = "unlimited" if int(cfg.rounds or 0) <= 0 else str(cfg.rounds)
    print(f"[cli] timeout: {cfg.timeout}s  rounds: {rounds_label}")
    if cfg.project_id:
        print(f"[cli] project: {cfg.project_id}  bot: {cfg.bot_id}")
    print("-" * 60)

    if not _acquire_cli():
        print("[cli] another CLI session holds the CLI data lock — aborting.", flush=True)
        return 2

    try:
        configure_cli(cfg)
        provider = _build_provider(cfg.host, cfg.model)
        set_provider(provider)
        tools = tools_schema_for(
            "developer",
            project_id=cfg.project_id,
            bot_id=cfg.bot_id,
        )

        if args.mode == "run":
            _cli_abort_flag.clear()
            _one_shot(
                " ".join(args.task),
                provider,
                tools,
                cfg.timeout,
                cfg.rounds,
                project_id=cfg.project_id,
                bot_id=cfg.bot_id,
            )
        elif args.mode == "repl":
            _prompt_loop(
                history=[],
                provider=provider,
                tools=tools,
                timeout=cfg.timeout,
                rounds=cfg.rounds,
            )
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        print("\n[cli] interrupted.", flush=True)
        _cli_abort_flag.set()
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
