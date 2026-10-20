"""The system-tray icon for browser mode (``python -m backend --ui --tray``).

CatCrewAI runs without a console window; the tray icon is how you reach it. Closing the browser tab
leaves it running here. Menu: **Open CatCrewAI** (also a double-click / left click) opens the web UI,
**Stop CatCrewAI** shuts the engine down. When the engine stops by itself (Power off in the UI),
the icon goes away too.

Uses ``pystray`` + Pillow (``requirements.txt``); without them, or without a desktop session (a
server), browser mode simply runs with no icon.
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

from backend.config import BASE_DIR

ICON = BASE_DIR / "frontend" / "src-tauri" / "icons" / "icon.png"


def available() -> str:
    """'' when a tray icon can be shown, else why not."""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - missing package or no display backend
        return f"no tray icon ({type(exc).__name__}: {exc}); pip install pystray pillow"
    return ""


def _image():
    from PIL import Image

    if ICON.is_file():
        return Image.open(ICON)
    return Image.new("RGBA", (64, 64), (56, 189, 248, 255))  # the cyan accent, if the icon is missing


def run(url: str, *, stop_engine: Callable[[], None], engine_alive: Callable[[], bool], poll: float = 1.0) -> None:
    """Show the icon until Stop is chosen or the engine ends. Blocks; call it on the main thread
    (macOS requires that)."""
    import pystray

    def open_ui(icon=None, item=None) -> None:  # noqa: ARG001 - pystray's callback signature
        webbrowser.open(url)

    def stop(icon, item=None) -> None:  # noqa: ARG001
        stop_engine()
        icon.stop()

    icon = pystray.Icon(
        "catcrew",
        _image(),
        "CatCrewAI",
        menu=pystray.Menu(
            pystray.MenuItem("Open CatCrewAI", open_ui, default=True),
            pystray.MenuItem(f"Running at {url}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop CatCrewAI", stop),
        ),
    )

    def watch() -> None:
        # Power off in the UI (or a crash) ends the engine: take the icon away with it.
        stopped = threading.Event()
        while not stopped.wait(poll):
            if not engine_alive():
                icon.stop()
                return

    threading.Thread(target=watch, daemon=True, name="catcrew-tray-watch").start()
    icon.run()


def url_file() -> Path:
    """Where browser mode writes its address, so a second start can open the running one."""
    return BASE_DIR / "data" / "ui.url"
