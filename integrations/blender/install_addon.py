#!/usr/bin/env python3
"""Set up the Blender side of CatCrewAI's Blender MCP integration (stdlib only, no Blender needed to run it).

CatCrewAI talks to Blender through the ``mcp-blender`` server (a separate, optional pip package), which talks
over a local socket to an add-on running *inside* Blender. That add-on has no "start when Blender opens"
option, so this installer does three things in every Blender config folder it finds:

  1. puts the add-on (``blender_mcp_addon``) into ``scripts/addons/``;
  2. writes ``scripts/startup/catcrew_mcp_autostart.py``, which enables the add-on and starts its socket server
     a moment after Blender opens (so Pixel finds Blender ready);
  3. optionally installs the ``mcp-blender`` server (``--install-server``).

    python integrations/blender/install_addon.py                       # download the release zip, install
    python integrations/blender/install_addon.py --source addon.zip    # or a zip / folder you already have
    python integrations/blender/install_addon.py --blender-version 4.2 --port 9876 --install-server
    python integrations/blender/install_addon.py --dry-run             # show what would happen
    python integrations/blender/install_addon.py --uninstall

Restart Blender afterwards (or press N in the 3D viewport and click Start Server once).
The add-on and server are MIT/AGPL software from https://github.com/RFingAdam/mcp-blender; nothing is bundled here.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

MODULE = "blender_mcp_addon"
RELEASE_URL = "https://github.com/RFingAdam/mcp-blender/releases/latest/download/blender_mcp_addon.zip"
STARTUP_NAME = "catcrew_mcp_autostart.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
_VERSION_DIR = re.compile(r"^\d+\.\d+$")

AUTOSTART = '''\
"""Written by CatCrewAI (integrations/blender/install_addon.py). Delete this file to stop auto-starting."""

import bpy

MODULE = "{module}"
HOST = "{host}"
PORT = {port}
_left = {{"tries": 40}}


def _say(text):
    print("[CatCrewAI] " + text)


def _tune():
    """Best effort: point the add-on at our host/port when it exposes such a setting (default is 127.0.0.1:9876)."""
    holders = [bpy.context.scene]
    try:
        holders.append(bpy.context.preferences.addons[MODULE].preferences)
    except Exception:
        pass
    for holder in holders:
        for name, value in (("mcp_host", HOST), ("host", HOST), ("mcp_port", PORT), ("port", PORT)):
            if hasattr(holder, name):
                try:
                    setattr(holder, name, value)
                except Exception:
                    pass


def _start():
    if bpy.app.background:
        return None
    _left["tries"] -= 1
    try:
        if MODULE not in bpy.context.preferences.addons:
            bpy.ops.preferences.addon_enable(module=MODULE)
        op = getattr(getattr(bpy.ops, "mcp", None), "start_server", None)
        if op is None:
            raise RuntimeError("the add-on has no start_server operator yet")
        _tune()
        op()
        _say("Blender MCP server started on %s:%s" % (HOST, PORT))
        return None
    except Exception as exc:  # the UI may not be ready yet: try again a little later
        if _left["tries"] <= 0:
            _say("could not start the Blender MCP server (%s). Open the 3D viewport, press N and click Start Server." % exc)
            return None
        return 1.5


bpy.app.timers.register(_start, first_interval=2.0, persistent=True)
'''


# --------------------------------------------------------------------------------------- locating Blender


def config_root(platform: str | None = None, env: dict[str, str] | None = None, home: Path | None = None) -> Path:
    """The folder that holds one sub-folder per Blender version (``4.2``, ``4.3`` ...)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform.startswith("win"):
        return Path(env.get("APPDATA") or home / "AppData" / "Roaming") / "Blender Foundation" / "Blender"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Blender"
    return Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "blender"


def version_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found = [p for p in root.iterdir() if p.is_dir() and _VERSION_DIR.match(p.name)]
    return sorted(found, key=lambda p: tuple(int(x) for x in p.name.split(".")))


def pick_targets(root: Path, version: str | None, config_dir: Path | None, create: bool) -> list[Path]:
    """Which config folders to install into: an explicit one, a chosen version, or every version found."""
    if config_dir:
        return [config_dir]
    if version:
        target = root / version
        return [target] if target.is_dir() or create else []
    return version_dirs(root)


# --------------------------------------------------------------------------------------- getting the add-on


def _find_addon_root(folder: Path) -> Path | None:
    """The directory that holds the add-on's ``__init__.py`` (with ``bl_info``) inside an unpacked download."""
    if (folder / "__init__.py").is_file():
        return folder
    for init in sorted(folder.rglob("__init__.py"), key=lambda p: len(p.parts)):
        try:
            if "bl_info" in init.read_text(encoding="utf-8", errors="replace"):
                return init.parent
        except OSError:
            continue
    return None


def fetch_source(source: str | None, workdir: Path) -> Path:
    """Return a folder containing the add-on package, from ``--source`` (zip or folder) or the release download."""
    if source and Path(source).is_dir():
        found = _find_addon_root(Path(source))
        if not found:
            raise SystemExit(f"{source} does not contain an add-on (no __init__.py with bl_info).")
        return found
    archive = Path(source) if source else workdir / "blender_mcp_addon.zip"
    if not source:
        print(f"Downloading {RELEASE_URL}")
        try:
            with urllib.request.urlopen(RELEASE_URL, timeout=60) as reply, open(archive, "wb") as out:  # noqa: S310 (fixed https URL)
                shutil.copyfileobj(reply, out)
        except OSError as exc:
            raise SystemExit(f"Download failed ({exc}). Get blender_mcp_addon.zip from the project's releases page and pass --source <zip>.")
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise SystemExit(f"{archive} is not a zip file.")
    unpacked = workdir / "unpacked"
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():  # refuse paths that would leave the folder
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise SystemExit(f"Refusing to unpack {name!r}: unsafe path in the zip.")
        bundle.extractall(unpacked)
    found = _find_addon_root(unpacked)
    if not found:
        raise SystemExit(f"{archive} does not contain an add-on (no __init__.py with bl_info).")
    return found


# --------------------------------------------------------------------------------------- installing


def install_into(config: Path, addon: Path, host: str, port: int, dry_run: bool) -> list[str]:
    """Copy the add-on and write the autostart script under one Blender config folder. Returns what was done."""
    addons = config / "scripts" / "addons" / MODULE
    startup = config / "scripts" / "startup" / STARTUP_NAME
    done = [f"add-on   -> {addons}", f"autostart -> {startup}"]
    if dry_run:
        return done
    addons.parent.mkdir(parents=True, exist_ok=True)
    if addons.exists():
        shutil.rmtree(addons)
    shutil.copytree(addon, addons, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    startup.parent.mkdir(parents=True, exist_ok=True)
    startup.write_text(AUTOSTART.format(module=MODULE, host=host, port=int(port)), encoding="utf-8")
    return done


def uninstall_from(config: Path, dry_run: bool) -> list[str]:
    removed = []
    for path in (config / "scripts" / "addons" / MODULE, config / "scripts" / "startup" / STARTUP_NAME):
        if path.exists():
            removed.append(str(path))
            if not dry_run:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
    return removed


def install_server() -> int:
    print("Installing the mcp-blender server (AGPL-3.0, separate package) with pip ...")
    return subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade", "mcp-blender"])


# --------------------------------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Install the Blender MCP add-on and its autostart script.")
    ap.add_argument("--source", help="add-on zip or folder (default: download the latest release zip)")
    ap.add_argument("--blender-version", help="only this Blender version folder, e.g. 4.2 (created if missing)")
    ap.add_argument("--config-dir", type=Path, help="a Blender config folder to use instead of searching for one")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"socket host (default {DEFAULT_HOST})")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"socket port (default {DEFAULT_PORT})")
    ap.add_argument("--install-server", action="store_true", help="also pip-install the mcp-blender server")
    ap.add_argument("--uninstall", action="store_true", help="remove the add-on and the autostart script")
    ap.add_argument("--dry-run", action="store_true", help="show what would be done, change nothing")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = config_root()
    targets = pick_targets(root, args.blender_version, args.config_dir, create=bool(args.blender_version))
    if not targets:
        print(
            f"No Blender config folder found under {root}.\n"
            "Start Blender once so it creates it, or pass --blender-version <x.y> or --config-dir <folder>."
        )
        return 2
    prefix = "[dry run] " if args.dry_run else ""

    if args.uninstall:
        for config in targets:
            removed = uninstall_from(config, args.dry_run)
            for path in removed:
                print(f"{prefix}removed {path}")
            if not removed:
                print(f"Nothing to remove in {config}")
        return 0

    with tempfile.TemporaryDirectory(prefix="catcrew-blender-") as tmp:
        addon = fetch_source(args.source, Path(tmp)) if not args.dry_run or args.source else Path(tmp)
        for config in targets:
            print(f"{prefix}Blender config: {config}")
            for line in install_into(config, addon, args.host, args.port, args.dry_run):
                print(f"{prefix}  {line}")
    if args.install_server and not args.dry_run:
        code = install_server()
        if code:
            print("pip could not install mcp-blender; run: pip install mcp-blender")
    print(f"{prefix}Done. Restart Blender. The server listens on {args.host}:{args.port}; CatCrewAI reads the same values from")
    print("CATCREW_BLENDER_HOST / CATCREW_BLENDER_PORT (Models -> MCP Integrations -> Refresh / Reconnect).")
    if not args.install_server:
        print("The CatCrewAI side needs the server too: pip install mcp-blender (or re-run with --install-server).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
