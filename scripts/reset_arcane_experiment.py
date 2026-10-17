#!/usr/bin/env python
"""Wipe Arcane Test Project + D:\\laragon\\www\\team-arcane, then recreate empty computer."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jinx_ai.platform.registry import delete_project, load_registry, reset_registry_cache
from jinx_ai.sessions import delete_session, list_sessions, load_session
from jinx_ai.settings_store import save_agent_settings, save_role_brain

FOLDER = Path(r"D:\laragon\www\team-arcane")
MODEL = "google_gemma-3-12b-it-Q4_K_M"


def main() -> int:
    save_agent_settings({"max_rounds": 0})
    save_role_brain("companion", "local", MODEL)
    reset_registry_cache()
    registry = load_registry()
    doomed = [
        row.id
        for row in registry.projects.values()
        if "arcane test" in row.name.lower()
        or any("team-arcane" in str(path).lower() for path in row.folders)
    ]
    print("projects", doomed, flush=True)
    for pid in doomed:
        for item in list_sessions():
            sid = str(item.get("id") or "")
            session = load_session(sid) if sid else None
            if session and str(session.get("project_id") or "") == pid:
                delete_session(sid)
                print("deleted session", sid, flush=True)
        delete_project(pid)
        print("deleted project", pid, flush=True)
    FOLDER.mkdir(parents=True, exist_ok=True)
    for child in list(FOLDER.iterdir()):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            if child.exists():
                os.system(f'cmd /c rmdir /s /q "{child}"')
        else:
            child.unlink(missing_ok=True)
    print("folder left", list(FOLDER.iterdir()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
