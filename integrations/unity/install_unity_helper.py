#!/usr/bin/env python3
"""Copy CatCrewUnityMCP.cs into a Unity project (stdlib only).

    python integrations/unity/install_unity_helper.py --project C:/Games/MyGame
    python integrations/unity/install_unity_helper.py --project C:/Games/MyGame --add-package   # also add "MCP for Unity"
    python integrations/unity/install_unity_helper.py --project C:/Games/MyGame --uninstall

The helper goes to ``Assets/Editor/CatCrewUnityMCP.cs``; Unity compiles it the next time the Editor has focus.
``--add-package`` adds https://github.com/CoplayDev/unity-mcp (folder MCPForUnity) to ``Packages/manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent / "CatCrewUnityMCP.cs"
PACKAGE_NAME = "com.coplaydev.unity-mcp"
PACKAGE_URL = "https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main"


def is_unity_project(project: Path) -> bool:
    return (project / "Assets").is_dir() and (project / "ProjectSettings").is_dir()


def add_package(project: Path, dry_run: bool) -> str:
    manifest = project / "Packages" / "manifest.json"
    if not manifest.is_file():
        return f"no {manifest} to edit (open the project in Unity once, then re-run)"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    deps = data.setdefault("dependencies", {})
    if PACKAGE_NAME in deps:
        return "MCP for Unity is already in Packages/manifest.json"
    if not dry_run:
        deps[PACKAGE_NAME] = PACKAGE_URL
        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "added MCP for Unity to Packages/manifest.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the CatCrewAI Unity MCP editor helper.")
    ap.add_argument("--project", type=Path, required=True, help="the Unity project folder (the one with Assets/)")
    ap.add_argument("--add-package", action="store_true", help="also add the MCP for Unity package to the project")
    ap.add_argument("--uninstall", action="store_true", help="remove the helper")
    ap.add_argument("--dry-run", action="store_true", help="show what would be done, change nothing")
    args = ap.parse_args(argv)

    project: Path = args.project.expanduser()
    if not is_unity_project(project):
        print(f"{project} does not look like a Unity project (needs Assets/ and ProjectSettings/).")
        return 2
    target = project / "Assets" / "Editor" / HELPER.name
    prefix = "[dry run] " if args.dry_run else ""

    if args.uninstall:
        for path in (target, target.with_name(target.name + ".meta")):
            if path.exists():
                print(f"{prefix}removed {path}")
                if not args.dry_run:
                    path.unlink()
        return 0

    print(f"{prefix}helper -> {target}")
    if not args.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HELPER, target)
    if args.add_package:
        print(f"{prefix}{add_package(project, args.dry_run)}")
    print(f"{prefix}Done. Open the project in Unity: the menu CatCrewAI > Unity MCP shows Start / Stop / Status.")
    print("CatCrewAI reaches the server at http://localhost:8080/mcp (change with CATCREW_UNITY_MCP_URL).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
