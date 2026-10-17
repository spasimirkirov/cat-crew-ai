"""Instruct Jinx's agent loop until she changes her title (no hand-edits)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jinx_ai.config import BASE_DIR
from jinx_ai.harness.approvals import set_approval_hook
from jinx_ai.harness.ask_user import set_ask_user_hook
from jinx_ai.harness.deferred import clear_loaded_tools
from jinx_ai.harness.runtime import set_abort, set_provider
from jinx_ai.providers import LocalLlamaProvider
from jinx_ai.tools import build_system_prompt, execute_tool, tools_schema_for
from jinx_ai.trace import bind_session, write_markdown
from jinx_ai.workspace import set_folders

NEW_TITLE = "Jinx AI - The Best AI Assistant"

TASK = f"""\
read_skill jinx-self.

Change YOUR custom title-bar brand to exactly:
{NEW_TITLE}

Mandatory tool sequence (do not skip, do not invent widgets.py):
1) read_file path=jinx_ai/titlebar.py
2) read_file path=jinx_ai/config.py
3) replace_in_file on titlebar.py — change QLabel("Jinx AI") to QLabel("{NEW_TITLE}")
4) replace_in_file on config.py — change WINDOW_TITLE = "Jinx AI" to WINDOW_TITLE = "{NEW_TITLE}"
5) run_command command="python -m compileall -q jinx_ai"
6) Only after replaces succeeded: restart_jinx alone

jinx_ai/... is always YOUR install at {BASE_DIR} even if workspace is Laragon.
"""


def _title_done() -> bool:
    titlebar = (BASE_DIR / "jinx_ai" / "titlebar.py").read_text(encoding="utf-8")
    config = (BASE_DIR / "jinx_ai" / "config.py").read_text(encoding="utf-8")
    return NEW_TITLE in titlebar and NEW_TITLE in config


def main() -> int:
    laragon = Path(r"D:\laragon\www\jinx-ai")
    set_folders([laragon] if laragon.is_dir() else [])
    bind_session("instruct-title")
    clear_loaded_tools()
    set_approval_hook(lambda _n, _a: "allow")
    set_ask_user_hook(lambda _p, _c: "continue — you decide")

    provider = LocalLlamaProvider(model="google_gemma-3-12b-it-Q4_K_M")
    set_provider(provider)
    set_abort(lambda: False)

    tools = tools_schema_for("developer")
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(mode="developer")},
        {"role": "user", "content": TASK},
    ]

    def on_tools(names: list[str]) -> None:
        print(f"[tools] {', '.join(names)}", flush=True)

    print(f"[jinx] instruct title -> {NEW_TITLE!r}", flush=True)
    reply = ""
    for attempt in range(1, 6):
        if _title_done():
            print("[jinx] title already set on disk", flush=True)
            break
        print(f"[jinx] attempt {attempt}/5", flush=True)
        try:
            reply = provider.run_agent(
                messages,
                tools,
                execute_tool,
                max_rounds=10,
                on_tools=on_tools,
            )
        except RuntimeError as exc:
            print(f"[jinx] agent error: {exc}", flush=True)
            reply = str(exc)
        print(f"[jinx] title_done={_title_done()}", flush=True)
        if _title_done():
            break
        messages.append({"role": "assistant", "content": (reply or "")[:2000]})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"STOP exploring. title_done=False. "
                    f"Call replace_in_file NOW on jinx_ai/titlebar.py and jinx_ai/config.py "
                    f"so both contain {NEW_TITLE!r}. Then restart_jinx alone."
                ),
            }
        )

    write_markdown("instruct-title")
    print("\n===== LAST AGENT REPLY =====\n", flush=True)
    print(reply or "(empty)", flush=True)
    ok = _title_done()
    print(f"[jinx] final title_done={ok}", flush=True)
    if ok and "Restarting" not in (reply or ""):
        # Title set but she may not have restarted — ask one more short turn.
        print("[jinx] requesting restart_jinx…", flush=True)
        try:
            messages.append({"role": "assistant", "content": reply or "done"})
            messages.append(
                {
                    "role": "user",
                    "content": "Title files are correct. Call restart_jinx now (only that tool).",
                }
            )
            reply = provider.run_agent(
                messages, tools, execute_tool, max_rounds=4, on_tools=on_tools
            )
        except RuntimeError as exc:
            print(f"[jinx] restart turn error: {exc}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
