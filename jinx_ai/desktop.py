"""Desktop actions — MCP ideas stolen into Python (no Node runtime)."""

from __future__ import annotations

import os
import re
import subprocess
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

from .config import BASE_DIR

_URL_OK = ("http://", "https://")
_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+\-]*$")


def open_url(url: str) -> str:
    target = (url or "").strip()
    if not target.startswith(_URL_OK):
        return "Error: url must start with http:// or https://"
    host = urlparse(target).netloc
    webbrowser.open(target)
    return f"Opened {host or target} in the default browser."


def clipboard(action: str = "get", text: str = "") -> str:
    kind = (action or "get").strip().lower()
    if kind in {"get", "read"}:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            timeout=12,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            return f"Error reading clipboard: {err or completed.returncode}"
        body = completed.stdout or ""
        if not body.strip():
            return "(clipboard is empty)"
        return body if len(body) <= 8000 else body[:8000] + "\n... [truncated]"
    if kind in {"set", "write"}:
        payload = text if text is not None else ""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", "Set-Clipboard -Value $input"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=12,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            return f"Error setting clipboard: {err or completed.returncode}"
        return f"Clipboard set ({len(payload)} characters)."
    return "Error: action must be get or set."


def notify(title: str = "Jinx", body: str = "") -> str:
    headline = (title or "Jinx").strip()[:80] or "Jinx"
    message = (body or "").strip()[:280] or "Ping."
    env = os.environ.copy()
    env["JINX_TOAST_TITLE"] = headline
    env["JINX_TOAST_BODY"] = message
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        "$n.ShowBalloonTip(5000, $env:JINX_TOAST_TITLE, $env:JINX_TOAST_BODY, "
        "[System.Windows.Forms.ToolTipIcon]::Info); "
        "Start-Sleep -Seconds 6; $n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return f"Error showing notification: {exc}"
    return f"Notification: {headline}"


def screenshot() -> str:
    folder = BASE_DIR / "data" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"shot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    env = os.environ.copy()
    env["JINX_SHOT_PATH"] = str(path)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
        "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
        "$bmp.Save($env:JINX_SHOT_PATH); $g.Dispose(); $bmp.Dispose(); "
        "Write-Output $env:JINX_SHOT_PATH"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Error taking screenshot: {exc}"
    if completed.returncode != 0 or not path.is_file():
        err = (completed.stderr or completed.stdout or "").strip()
        return f"Error taking screenshot: {err or completed.returncode}"
    return f"Saved screenshot to {path} ({path.stat().st_size} bytes)."


def system_time(timezone: str = "") -> str:
    from .here import locale_city

    now = datetime.now().astimezone()
    lines = [f"Local: {now.isoformat(timespec='seconds')} ({now.tzname() or 'local'})"]
    city = locale_city()
    if city:
        lines.append(f"Home city: {city}")
    zone = (timezone or "").strip()
    if zone:
        try:
            from zoneinfo import ZoneInfo

            other = now.astimezone(ZoneInfo(zone))
            lines.append(f"{zone}: {other.isoformat(timespec='seconds')}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Could not convert to {zone}: {exc}")
    return "\n".join(lines)


def looks_like_program_name(name: str) -> bool:
    return bool(_NAME_OK.fullmatch((name or "").strip()))
