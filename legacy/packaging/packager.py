#!/usr/bin/env python3
"""CatCrewAI Packager — builds Windows installer."""

from __future__ import annotations

import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
INNO_DIR = PROJECT_ROOT / "packaging" / "inno"
CATCREW_EXE_NAME = "CatCrewAI.exe"
INSTALLER_NAME = "CatCrewAI-Setup.exe"

# Use the .venv python that has pip + PyInstaller
PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
PIP = str(PROJECT_ROOT / ".venv" / "Scripts" / "pip.exe")


def info(msg: str) -> None:
    print(f"[packager] {msg}")


def warn(msg: str) -> None:
    print(f"[packager WARNING] {msg}", file=sys.stderr)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    info(f"Running: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        for line in result.stdout.splitlines():
            info(line)
    if result.stderr:
        for line in result.stderr.splitlines():
            warn(line)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed (exit {result.returncode})")


def clean() -> None:
    info("Cleaning dist and build directories...")
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    info("Clean complete.")


def install_requirements() -> None:
    req_file = PROJECT_ROOT / "requirements.txt"
    if not req_file.exists():
        warn("No requirements.txt found, skipping.")
        return
    info(f"Installing requirements: {req_file}")
    run([PYTHON, "-m", "pip", "install", "-r", str(req_file)])


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller as _p  # noqa: F401
        info(f"PyInstaller already available ({_p.__version__})")
    except ImportError:
        info("Installing PyInstaller...")
        run([PYTHON, "-m", "pip", "install", "pyinstaller"])
        import PyInstaller as _p  # noqa: F401
        info(f"PyInstaller installed ({_p.__version__})")


def build_executable() -> Path:
    info("Building CatCrewAI executable with PyInstaller...")
    cmd = [
        PYTHON, "-m", "PyInstaller",
        "--name", CATCREW_EXE_NAME,
        "--onedir", "--clean", "--noconfirm",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / "pyinstaller_work"),
        "--specpath", str(BUILD_DIR / "pyinstaller_spec"),
    ]
    for mod in [
        "backend", "backend.harness", "backend.providers",
        "backend.connectors", "backend.connectors.drivers",
        "backend.services.lm_runtime",
    ]:
        cmd += ["--collect-submodules", mod]
    for mod in [
        "backend.config", "backend.services.settings_store", "backend.services.here",
        "backend.services.llama_server", "backend.services.memory", "backend.heartbeat",
        "backend.wake", "backend.stt", "backend.voice", "backend.body",
        "backend.orb_particles", "backend.avatar_window", "backend.services.comfyui",
        "backend.helpers", "backend.tools", "backend.services.todo",
        "backend.services.clarify", "backend.single_instance",
        "backend.services.pending_restart", "backend.services.self_modifier",
        "backend.providers.base", "backend.providers.openai_compat",
        "backend.providers.local_llama", "backend.providers.deepseek",
        "backend.providers.groq", "backend.providers.gemini",
        "backend.providers.ollama", "backend.services.lm_runtime", "backend.lm_panel",
        "backend.services.local_models", "backend.services.gguf_browser", "backend.services.downloads",
        "backend.download_flyout", "backend.services.hardware", "backend.services.model_recs",
        "backend.services.docker_runtime", "backend.services.api_access",
        "backend.api_access_page", "backend.connectors_page",
        "backend.skills_page", "backend.services.terminal", "backend.git_panel",
        "backend.services.git_ops", "backend.db_panel", "backend.db_backend",
        "backend.file_explorer", "backend.editor_pane",
        "backend.chat_window", "backend.chat_transcript",
        "backend.composer", "backend.activity", "backend.activity_timeline",
        "backend.approval_bar", "backend.ask_user_bar", "backend.services.trace",
        "backend.services.session_tree", "backend.services.session_fs", "backend.services.sessions",
        "backend.services.workspace", "backend.agent_context", "backend.services.searxng",
        "backend.services.ddgs_search", "backend.services.image_search",
        "backend.skill_loader", "backend.prompt_loader", "backend.export",
        "backend.icon_loader", "backend.overlay", "backend.theme",
        "backend.updates", "backend.auto_update", "backend.telemetry",
        "backend.performance", "backend.backup", "backend.migration",
        "backend.plugins", "backend.plugin_manager",
        "backend.extensions", "backend.extension_manager",
        "backend.integration", "backend.proxy", "backend.firewall",
        "backend.security", "backend.encryption", "backend.credentials",
        "backend.token_manager", "backend.authentication", "backend.oauth",
    ]:
        cmd += ["--hidden-import", mod]
    cmd += [
        "--add-data", f"{PROJECT_ROOT / 'requirements.txt'};.",
        "--add-data", f"{PROJECT_ROOT / '.env.example'};.",
        str(PROJECT_ROOT / "main.py"),
    ]
    run(cmd)
    exe_path = DIST_DIR / CATCREW_EXE_NAME / CATCREW_EXE_NAME
    if not exe_path.is_file():
        # PyInstaller with --name CatCrewAI --onedir creates dist/CatCrewAI.exe/CatCrewAI.exe
        exe_path = DIST_DIR / "CatCrewAI" / CATCREW_EXE_NAME
    if not exe_path.is_file():
        raise FileNotFoundError(f"Executable not found: {exe_path}")
    info("=" * 40)
    info(f"EXE: {exe_path}")
    info(f"SIZE: {exe_path.stat().st_size / (1024*1024):.1f} MB")
    info("=" * 40)


def create_cli_wrapper() -> Path:
    wrapper = PROJECT_ROOT / "packaging" / "catcrew.cmd"
    lines = [
        "@echo off",
        "REM CatCrewAI CLI - Terminal command wrapper",
        "",
        "set CATCREW_HOME=\"%~dp0..\\dist\\CatCrewAI.exe\"",
        "set CATCREW_MODELS_DIR=\"%~dp0..\\dist\\CatCrewAI.exe\\models\"",
        "",
        "\"%CATCREW_HOME%\\CatCrewAI.exe\" %*",
    ]
    content = "\r\n".join(lines) + "\r\n"
    wrapper.write_text(content, encoding="utf-8")
    info(f"Created CLI wrapper: {wrapper}")
    return wrapper


INNISETUP_TEMPLATE = r"""; CatCrewAI Installer Script
; Generated by packager.py on __TIMESTAMP__
; To build: open in Inno Setup Compiler or run: ISCC.exe CatCrewAI.iss

#define MyAppName "CatCrewAI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CatCrewAI"
#define MyAppURL "https://cat-crew-ai.local"

[Setup]
AppId={{8F1C2D3E-4A5B-6C7D-8E9F-0A1B2C3D4E5F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Absolute path so Inno Setup writes the installer to dist/ reliably
OutputDir=C:\Development\cat-crew-ai\dist
OutputBaseFilename=CatCrewAI-Setup

Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"
Name: "addtopath"; Description: "Add 'catcrew' to system PATH (enables terminal command)"; GroupDescription: "Command line access:"

[Files]
Source: "__EXE_PATH__"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\__CATCREW_EXE__"
Name: "{group}\Uninstall __APP_NAME__"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\__CATCREW_EXE__"; Tasks: desktopicon

[Run]
Filename: "{app}\__CATCREW_EXE__"; Description: "Launch CatCrewAI"; Flags: postinstall nowait skipifsilent
"""


def create_inno_script(exe_path: Path) -> Path:
    info("Creating Inno Setup script...")
    INNO_DIR.mkdir(parents=True, exist_ok=True)
    # Write the pre-built CatCrewAI.iss directly (it already has correct paths)
    script_path = INNO_DIR / "CatCrewAI.iss"
    # The CatCrewAI.iss was pre-written with correct absolute paths.
    # Just verify it exists; don't regenerate from template.
    if not script_path.is_file():
        raise FileNotFoundError(f"Inno script not found: {script_path}")
    info(f"Inno Setup script: {script_path}")
    return script_path


def find_inno_setup() -> Path | None:
    for p in [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 5\ISCC.exe"),
    ]:
        if p.is_file():
            info(f"Found Inno Setup compiler: {p}")
            return p
    return None


def build_installer_with_inno(iss_path: Path) -> Path | None:
    compiler = find_inno_setup()
    if compiler is None:
        warn("Inno Setup not found. Skipping installer build.")
        warn("The .exe is ready in dist/CatCrewAI/ - distribute directly.")
        return None
    info(f"Building installer with Inno Setup: {compiler}")
    run([str(compiler), str(iss_path)])
    installer_path = DIST_DIR / INSTALLER_NAME
    if not installer_path.is_file():
        alt = DIST_DIR / "CatCrewAI-Setup-64.exe"
        if alt.is_file():
            installer_path = alt
    if installer_path.is_file():
        info(f"Installer created: {installer_path}")
    return installer_path if installer_path.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser(description="CatCrewAI Packager")
    parser.add_argument("--clean", action="store_true", help="Clean before building")
    parser.add_argument("--exe-only", action="store_true", help="Executable only, skip installer")
    parser.add_argument("--inno-only", action="store_true", help="Installer only (needs existing exe)")
    args = parser.parse_args()

    if args.clean:
        clean()

    if args.inno_only:
        exe_path = DIST_DIR / CATCREW_EXE_NAME / CATCREW_EXE_NAME
        if not exe_path.is_file():
            exe_path = DIST_DIR / "CatCrewAI" / CATCREW_EXE_NAME
        if not exe_path.is_file():
            raise FileNotFoundError(f"No CatCrewAI.exe found. Run full build first.")
        create_cli_wrapper()
        iss_path = create_inno_script(exe_path)
        build_installer_with_inno(iss_path)
        return

    install_requirements()
    ensure_pyinstaller()
    create_cli_wrapper()
    exe_path = build_executable()
    iss_path = create_inno_script(exe_path)

    if not args.exe_only:
        build_installer_with_inno(iss_path)
    else:
        info("Skipping Inno Setup installer (--exe-only)")

    info("=" * 60)
    info("BUILD SUMMARY")
    info(f"Executable: {exe_path}")
    installer = DIST_DIR / INSTALLER_NAME
    if installer.is_file():
        info(f"Installer: {installer}")
    else:
        info("Installer: NOT CREATED")
    info("=" * 60)


if __name__ == "__main__":
    main()
