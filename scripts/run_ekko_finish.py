#!/usr/bin/env python
"""1:1 Ekko turn — same path as clicking Ekko in Project Explorer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PYTHONUTF8", "1")

from jinx_ai.harness.approvals import set_approval_hook
from jinx_ai.harness.ask_user import set_ask_user_hook
from jinx_ai.harness.deferred import clear_loaded_tools
from jinx_ai.harness.runtime import set_abort, set_provider
from jinx_ai.platform.registry import get_bot, get_project, load_registry, reset_registry_cache
from jinx_ai.platform.runtime import set_bot_id, set_project_id
from jinx_ai.platform.runner import run_bot
from jinx_ai.providers import LocalLlamaProvider
from jinx_ai.trace import bind_session
from jinx_ai.workspace import set_folders

PROJECT_ID = "proj_da2211ea56"
FOLDER = Path(r"D:\laragon\www\team-arcane")
TASK = """
read_file index.html. It is a BROKEN fragment (only team divs, no <html>).
write_file the COMPLETE file: <!DOCTYPE html> through </html>.

Must include:
- Bootstrap 5 CDN + Font Awesome CDN
- body background #090b12, text white, accents #22d3ee and #f472b6
- Hero: Team Arcane tagline + CTA
- Services: Architecture, Frontend/Backend, QA, DevOps, Growth, Legal/Finance
- Team cards for ALL 13: Jayce (Team Lead), Heimerdinger, Vi, Jinx, Ekko, Caitlyn,
  Silco, Mel, Cassandra, Draven, Blitzcrank, Singed, Braum — keep the bios already
  in the fragment, add Jayce.
- Contact form (name, email, message)
Call write_file once with the full document. Function calling, not printed JSON.
"""


def main() -> int:
    reset_registry_cache()
    project = get_project(PROJECT_ID)
    bot = get_bot("ekko")
    if project is None or bot is None:
        print("missing project or ekko", flush=True)
        return 2
    set_folders([FOLDER])
    set_project_id(project.id)
    set_bot_id(bot.id)
    bind_session("ekko-finish")
    clear_loaded_tools()
    set_approval_hook(lambda _n, _a: "allow")
    set_ask_user_hook(lambda _p, _c: "Keep working. You decide.")
    set_abort(lambda: False)

    provider = LocalLlamaProvider(model="google_gemma-3-12b-it-Q4_K_M")
    set_provider(provider)
    print(f"[ekko] project {project.name} computer {FOLDER}", flush=True)

    def on_tools(names: list[str]) -> None:
        print(f"[ekko tools] {', '.join(names)}", flush=True)

    # run_bot does not take on_tools; activity prints via emit. Wrap provider.
    orig = provider.run_agent

    def wrapped(*args, **kwargs):
        kwargs.setdefault("on_tools", on_tools)
        kwargs["max_rounds"] = 0
        return orig(*args, **kwargs)

    provider.run_agent = wrapped  # type: ignore[method-assign]
    out = run_bot(
        bot,
        TASK.strip(),
        project=project,
        registry=load_registry(),
        max_rounds=0,
    )
    print(out, flush=True)
    html_text = (FOLDER / "index.html").read_text(encoding="utf-8", errors="replace")
    names = (
        "Jayce",
        "Jinx",
        "Ekko",
        "Caitlyn",
        "Heimerdinger",
        "Vi",
        "Silco",
        "Mel",
        "Cassandra",
        "Draven",
        "Blitzcrank",
        "Singed",
        "Braum",
    )
    found = [n for n in names if n in html_text]
    print(f"[ekko] roster in index.html: {len(found)}/13 {found}", flush=True)
    return 0 if len(found) >= 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
