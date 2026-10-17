#!/usr/bin/env python
"""Create Arcane Test Project the same way New Project does, then run Chief."""

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
from jinx_ai.platform.models import Project, new_id
from jinx_ai.platform.registry import (
    delete_project,
    load_registry,
    reset_registry_cache,
    save_project,
)
from jinx_ai.platform.runtime import set_bot_id, set_project_id
from jinx_ai.providers import LocalLlamaProvider
from jinx_ai.session_tree import append_turn, provider_history
from jinx_ai.sessions import (
    bind_project_computer,
    delete_session,
    list_sessions,
    load_session,
    new_session,
    save_session,
)
from jinx_ai.settings_store import save_agent_settings, save_role_brain
from jinx_ai.tools import build_system_prompt, execute_tool, tools_schema_for
from jinx_ai.trace import bind_session, write_markdown
from jinx_ai.workspace import prepare_computer_folder, set_folders

FOLDER = Path(r"D:\laragon\www\team-arcane")
PROJECT_NAME = "Arcane Test Project"
MODEL = "google_gemma-3-12b-it-Q4_K_M"
MAX_OUTER = 8
KICKOFF = """\
Build a modern, high-tech agency landing page for "Team Arcane" showcasing us as an elite digital product team. Tech Stack Requirements: React.js (Vite setup preferred) Bootstrap 5 CSS FontAwesome icons Website Features & Sections: Hero Section: Catchy tagline, Hextech-inspired modern aesthetics, CTA button. Services/Capabilities: Highlight our full lifecycle capabilities (Architecture, Frontend/Backend, QA, DevOps, Growth, Legal/Finance). Interactive Team Roster: Card layout displaying all 13 team members with their names, titles, bios, and core skills. Contact/Kickoff Section: Clean UI form for potential client project inquiries. Execution Strategy for Lead: Assign Heimerdinger (Architect) to define the file layout and React component tree structure. Assign Jinx (Designer) to define the color scheme, Bootstrap theme customization, and visual layout hierarchy. Assign Ekko (Developer) to initialize the repo, install dependencies (bootstrap, @fortawesome), and build the components. Assign Caitlyn (QA) to audit component responsiveness and edge cases once implemented. Please return the initial plan, task assignments (owners), and the immediate next action step.
"""
CONTINUE = (
    "list_dir the shared computer. The site is NOT done until the ROOT folder has "
    "package.json with react, src files, and all 13 Arcane names. Do not scaffold "
    "into a nested vite-project folder. Call delegate_to_bot ekko: "
    "npm create vite@latest . -- --template react, then npm install bootstrap "
    "@fortawesome/fontawesome-free, then write Hero, Services, Team (list_roster), "
    "Contact. Then delegate_to_bot caitlyn to audit."
)

_ROSTER = (
    "Jayce",
    "Heimerdinger",
    "Vi",
    "Jinx",
    "Ekko",
    "Caitlyn",
    "Silco",
    "Mel",
    "Cassandra",
    "Draven",
    "Blitzcrank",
    "Singed",
    "Braum",
)


def _wipe_computer(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for child in list(root.iterdir()):
        try:
            if child.is_dir():
                import shutil

                shutil.rmtree(child, ignore_errors=True)
                if child.exists():
                    os.system(f'cmd /c rmdir /s /q "{child}"')
            else:
                child.unlink(missing_ok=True)
        except OSError as exc:
            print(f"[jinx] wipe skip {child}: {exc}", flush=True)


def _delete_old_project() -> None:
    reset_registry_cache()
    registry = load_registry()
    doomed = [
        row.id
        for row in registry.projects.values()
        if row.name.strip().lower() == PROJECT_NAME.lower()
        or any("team-arcane" in str(path).lower() for path in row.folders)
    ]
    for pid in doomed:
        for item in list_sessions():
            sid = str(item.get("id") or "")
            session = load_session(sid) if sid else None
            if session and str(session.get("project_id") or "") == pid:
                delete_session(sid)
        delete_project(pid)
        print(f"[jinx] deleted project {pid}", flush=True)
    reset_registry_cache()


def _site_ready(root: Path) -> bool:
    pkg = root / "package.json"
    if not pkg.is_file():
        return False
    text = pkg.read_text(encoding="utf-8", errors="replace").lower()
    if "react" not in text:
        return False
    src = root / "src"
    if not src.is_dir():
        return False
    blob_parts: list[str] = []
    for path in src.rglob("*"):
        if path.suffix.lower() not in {".js", ".jsx", ".ts", ".tsx", ".css", ".html"}:
            continue
        try:
            blob_parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    html = root / "index.html"
    if html.is_file():
        blob_parts.append(html.read_text(encoding="utf-8", errors="replace"))
    blob = "\n".join(blob_parts)
    return sum(1 for name in _ROSTER if name in blob) >= 10


def _ensure_project() -> Project:
    reset_registry_cache()
    folder = str(prepare_computer_folder(FOLDER))
    registry = load_registry()
    if "arcane" not in registry.teams:
        raise SystemExit("Arcane team is missing. Run python scripts/init_arcane_team.py")
    for row in registry.projects.values():
        same_name = row.name.strip().lower() == PROJECT_NAME.lower()
        same_folder = folder in [str(Path(p)) for p in row.folders]
        if same_name or same_folder:
            row.name = PROJECT_NAME
            row.folders = [folder]
            if "arcane" not in row.team_ids:
                row.team_ids.append("arcane")
            row.state["kickoff"] = KICKOFF.strip()
            save_project(row)
            return row
    project = Project(
        id=new_id("proj"),
        name=PROJECT_NAME,
        description="Team Arcane agency landing page.",
        team_ids=["arcane"],
        folders=[folder],
        state={"kickoff": KICKOFF.strip()},
    )
    save_project(project)
    return project


def main() -> int:
    saved = save_agent_settings({"max_rounds": 0})
    brains = save_role_brain("companion", "local", MODEL)
    print(f"[jinx] max_rounds={saved.get('max_rounds')} (0=unlimited)", flush=True)
    print(f"[jinx] chat_model={brains.get('chat_model')}", flush=True)

    print(f"[jinx] wiping {FOLDER}", flush=True)
    _delete_old_project()
    _wipe_computer(FOLDER)

    project = _ensure_project()
    print(f"[jinx] project {project.name} [{project.id}]", flush=True)
    print(f"[jinx] computer {project.folders[0]}", flush=True)

    session = new_session(title=f"{project.name} kickoff", activate=True)
    session["project_id"] = project.id
    session["bot_id"] = "chief"
    bind_project_computer(session, project.id)
    save_session(session, activate=True)
    bind_session(str(session.get("id") or ""))
    set_folders([Path(p) for p in project.folders])
    set_project_id(project.id)
    set_bot_id("chief")
    clear_loaded_tools()
    set_approval_hook(lambda _n, _a: "allow")
    set_ask_user_hook(lambda _p, _c: "Keep working. You decide.")
    set_abort(lambda: False)

    print(f"[jinx] brain local/{MODEL}", flush=True)
    provider = LocalLlamaProvider(model=MODEL)
    set_provider(provider)

    tools = tools_schema_for("developer", project_id=project.id, bot_id="chief")
    print(f"[jinx] chief tools: {len(tools)}", flush=True)

    def on_tools(names: list[str]) -> None:
        print(f"[tools] {', '.join(names)}", flush=True)

    history = provider_history(session)
    already = any(
        str(msg.get("content") or "").strip() == KICKOFF.strip()
        for msg in history
        if msg.get("role") == "user"
    )
    if not already:
        append_turn(session, "user", KICKOFF.strip())
        save_session(session, activate=True)
        history = provider_history(session)

    reply = ""
    for attempt in range(1, MAX_OUTER + 1):
        if _site_ready(FOLDER):
            print("[jinx] site already on disk", flush=True)
            break
        print(f"[jinx] turn {attempt}/{MAX_OUTER} ready={_site_ready(FOLDER)}", flush=True)
        messages: list[dict] = [
            {
                "role": "system",
                "content": build_system_prompt(
                    "developer",
                    project_id=project.id,
                    bot_id="chief",
                ),
            },
            *provider_history(session),
        ]
        try:
            reply = provider.run_agent(
                messages,
                tools,
                execute_tool,
                max_rounds=0,
                on_tools=on_tools,
            )
        except Exception as exc:  # noqa: BLE001
            reply = f"[agent error] {exc}"
            print(reply, flush=True)
        append_turn(session, "assistant", reply or "")
        save_session(session, activate=True)
        write_markdown(str(session.get("id") or "arcane-kickoff"))
        print(f"[jinx] site_ready={_site_ready(FOLDER)}", flush=True)
        preview = (reply or "").replace("\n", " ")[:400]
        print(f"[jinx] reply: {preview}", flush=True)
        if _site_ready(FOLDER):
            break
        append_turn(session, "user", CONTINUE)
        save_session(session, activate=True)

    write_markdown(str(session.get("id") or "arcane-kickoff"))
    ok = _site_ready(FOLDER)
    print(f"[jinx] final site_ready={ok}", flush=True)
    print("\n===== LAST CHIEF REPLY =====\n", flush=True)
    print(reply or "(empty)", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
