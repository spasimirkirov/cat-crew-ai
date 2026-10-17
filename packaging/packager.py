#!/usr/bin/env python3
"""Jinx AI Packager — builds Windows installer."""

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
JINX_EXE_NAME = "JinxAI.exe"
INSTALLER_NAME = "JinxAI-Setup.exe"

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
    info("Building Jinx AI executable with PyInstaller...")
    cmd = [
        PYTHON, "-m", "PyInstaller",
        "--name", JINX_EXE_NAME,
        "--onedir", "--clean", "--noconfirm",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / "pyinstaller_work"),
        "--specpath", str(BUILD_DIR / "pyinstaller_spec"),
    ]
    for mod in [
        "jinx_ai", "jinx_ai.harness", "jinx_ai.providers",
        "jinx_ai.connectors", "jinx_ai.connectors.drivers",
        "jinx_ai.lm_runtime",
    ]:
        cmd += ["--collect-submodules", mod]
    for mod in [
        "jinx_ai.config", "jinx_ai.settings_store", "jinx_ai.here",
        "jinx_ai.llama_server", "jinx_ai.memory", "jinx_ai.heartbeat",
        "jinx_ai.wake", "jinx_ai.stt", "jinx_ai.voice", "jinx_ai.body",
        "jinx_ai.orb_particles", "jinx_ai.avatar_window", "jinx_ai.comfyui",
        "jinx_ai.helpers", "jinx_ai.tools", "jinx_ai.todo",
        "jinx_ai.clarify", "jinx_ai.single_instance",
        "jinx_ai.pending_restart", "jinx_ai.self_modifier",
        "jinx_ai.providers.base", "jinx_ai.providers.openai_compat",
        "jinx_ai.providers.local_llama", "jinx_ai.providers.deepseek",
        "jinx_ai.providers.groq", "jinx_ai.providers.gemini",
        "jinx_ai.providers.ollama", "jinx_ai.lm_runtime", "jinx_ai.lm_panel",
        "jinx_ai.local_models", "jinx_ai.gguf_browser", "jinx_ai.downloads",
        "jinx_ai.download_flyout", "jinx_ai.hardware", "jinx_ai.model_recs",
        "jinx_ai.docker_runtime", "jinx_ai.api_access",
        "jinx_ai.api_access_page", "jinx_ai.connectors_page",
        "jinx_ai.skills_page", "jinx_ai.terminal", "jinx_ai.git_panel",
        "jinx_ai.git_ops", "jinx_ai.db_panel", "jinx_ai.db_backend",
        "jinx_ai.file_explorer", "jinx_ai.editor_pane",
        "jinx_ai.chat_window", "jinx_ai.chat_transcript",
        "jinx_ai.composer", "jinx_ai.activity", "jinx_ai.activity_timeline",
        "jinx_ai.approval_bar", "jinx_ai.ask_user_bar", "jinx_ai.trace",
        "jinx_ai.session_tree", "jinx_ai.session_fs", "jinx_ai.sessions",
        "jinx_ai.workspace", "jinx_ai.agent_context", "jinx_ai.searxng",
        "jinx_ai.ddgs_search", "jinx_ai.image_search",
        "jinx_ai.skill_loader", "jinx_ai.prompt_loader", "jinx_ai.export",
        "jinx_ai.icon_loader", "jinx_ai.overlay", "jinx_ai.theme",
        "jinx_ai.updates", "jinx_ai.auto_update", "jinx_ai.telemetry",
        "jinx_ai.performance", "jinx_ai.backup", "jinx_ai.migration",
        "jinx_ai.plugins", "jinx_ai.plugin_manager",
        "jinx_ai.extensions", "jinx_ai.extension_manager",
        "jinx_ai.integration", "jinx_ai.proxy", "jinx_ai.firewall",
        "jinx_ai.security", "jinx_ai.encryption", "jinx_ai.credentials",
        "jinx_ai.token_manager", "jinx_ai.authentication", "jinx_ai.oauth",
    ]:
        cmd += ["--hidden-import", mod]
    cmd += [
        "--add-data", f"{PROJECT_ROOT / 'requirements.txt'};.",
        "--add-data", f"{PROJECT_ROOT / '.env.example'};.",
        str(PROJECT_ROOT / "main.py"),
    ]
    run(cmd)
    exe_path = DIST_DIR / JINX_EXE_NAME / JINX_EXE_NAME
    if not exe_path.is_file():
        # PyInstaller with --name JinxAI --onedir creates dist/JinxAI.exe/JinxAI.exe
        exe_path = DIST_DIR / "JinxAI" / JINX_EXE_NAME
    if not exe_path.is_file():
        raise FileNotFoundError(f"Executable not found: {exe_path}")
    info("=" * 40)
    info(f"EXE: {exe_path}")
    info(f"SIZE: {exe_path.stat().st_size / (1024*1024):.1f} MB")
    info("=" * 40)


def create_cli_wrapper() -> Path:
    wrapper = PROJECT_ROOT / "packaging" / "jinx.cmd"
    lines = [
        "@echo off",
        "REM Jinx AI CLI - Terminal command wrapper",
        "",
        "set JINX_HOME=\"%~dp0..\\dist\\JinxAI.exe\"",
        "set JINX_MODELS_DIR=\"%~dp0..\\dist\\JinxAI.exe\\models\"",
        "",
        "\"%JINX_HOME%\\JinxAI.exe\" %*",
    ]
    content = "\r\n".join(lines) + "\r\n"
    wrapper.write_text(content, encoding="utf-8")
    info(f"Created CLI wrapper: {wrapper}")
    return wrapper


INNISETUP_TEMPLATE = r"""; Jinx AI Installer Script
; Generated by packager.py on __TIMESTAMP__
; To build: open in Inno Setup Compiler or run: ISCC.exe JinxAI.iss

#define MyAppName "Jinx AI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Jinx AI"
#define MyAppURL "https://jinx-ai.local"

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
OutputDir=C:\Development\jinx-ai\dist
OutputBaseFilename=JinxAI-Setup

Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"
Name: "addtopath"; Description: "Add 'jinx' to system PATH (enables terminal command)"; GroupDescription: "Command line access:"

[Files]
Source: "__EXE_PATH__"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\__JINX_EXE__"
Name: "{group}\Uninstall __APP_NAME__"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\__JINX_EXE__"; Tasks: desktopicon

[Run]
Filename: "{app}\__JINX_EXE__"; Description: "Launch Jinx AI"; Flags: postinstall nowait skipifsilent
"""


def create_inno_script(exe_path: Path) -> Path:
    info("Creating Inno Setup script...")
    INNO_DIR.mkdir(parents=True, exist_ok=True)
    # Write the pre-built JinxAI.iss directly (it already has correct paths)
    script_path = INNO_DIR / "JinxAI.iss"
    # The JinxAI.iss was pre-written with correct absolute paths.
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
        warn("The .exe is ready in dist/JinxAI/ - distribute directly.")
        return None
    info(f"Building installer with Inno Setup: {compiler}")
    run([str(compiler), str(iss_path)])
    installer_path = DIST_DIR / INSTALLER_NAME
    if not installer_path.is_file():
        alt = DIST_DIR / "JinxAI-Setup-64.exe"
        if alt.is_file():
            installer_path = alt
    if installer_path.is_file():
        info(f"Installer created: {installer_path}")
    return installer_path if installer_path.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Jinx AI Packager")
    parser.add_argument("--clean", action="store_true", help="Clean before building")
    parser.add_argument("--exe-only", action="store_true", help="Executable only, skip installer")
    parser.add_argument("--inno-only", action="store_true", help="Installer only (needs existing exe)")
    args = parser.parse_args()

    if args.clean:
        clean()

    if args.inno_only:
        exe_path = DIST_DIR / JINX_EXE_NAME / JINX_EXE_NAME
        if not exe_path.is_file():
            exe_path = DIST_DIR / "JinxAI" / JINX_EXE_NAME
        if not exe_path.is_file():
            raise FileNotFoundError(f"No JinxAI.exe found. Run full build first.")
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
