"""``python -m backend`` — run the API server on loopback.

    python -m backend                 # http://127.0.0.1:8765
    python -m backend --port 9000
    python -m backend --smoke         # start, check /api/health, exit 0/1
    python -m backend --ui            # browser mode: serve frontend/dist and open it
    python -m backend --ui --port 0   # ... on any free port
    pythonw -m backend --tray         # ... with a tray icon and no console (scripts/start.ps1 does this)

Browser mode needs the built front end (``cd frontend && npm run build``). With ``--ui`` the
default port falls back to a free one when 8765 is taken.

On loopback the API is the desktop app's private backend: no login. Any other address is
**remote mode** (``api/access.py``): every request needs a login, the first account (created with
a setup code printed here) is the admin, and further accounts come from admin invites. Put a TLS
proxy (Caddy, nginx) in front of a public server:

    python -m backend --ui --host 0.0.0.0 --port 8765
    python -m backend --ui --remote --no-browser   # behind Caddy/nginx on the same server

``scripts/vps/setup.sh`` sets up a server that way (Caddy with HTTPS, a systemd service).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m backend", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("CATCREW_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CATCREW_PORT", DEFAULT_PORT)))
    parser.add_argument("--smoke", action="store_true", help="start, probe /api/health, exit")
    parser.add_argument("--ui", action="store_true", default=os.environ.get("CATCREW_UI", "") == "1",
                        help="browser mode: serve the built front end (frontend/dist) and open it")
    parser.add_argument("--no-browser", action="store_true", help="with --ui: do not open a browser tab")
    parser.add_argument("--tray", action="store_true",
                        help="browser mode with a system-tray icon (open the UI, stop the engine); for pythonw, no console")
    parser.add_argument("--remote", action="store_true", default=os.environ.get("CATCREW_REMOTE", "") == "1",
                        help="require logins even on loopback: behind a reverse proxy (Caddy, nginx) on the same server")
    parser.add_argument("--log-level", default="warning")
    return parser.parse_args(argv)


def free_port(host: str = DEFAULT_HOST, preferred: int = 0) -> int:
    """``preferred`` when nothing listens on it, else a port the OS picks."""
    import socket

    for port in ((preferred, 0) if preferred else (0,)):
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    raise OSError("no free port")


def _open_browser(server, url: str) -> None:
    """Open ``url`` once the server accepts connections."""
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - no browser on this machine: the printed URL is enough
        print(f"[backend] could not open a browser ({exc}); open {url} yourself.", flush=True)


def _log_to_file() -> None:
    """``pythonw`` has no console: keep the engine's output in ``data/logs/backend.log``."""
    from backend.config import BASE_DIR

    logs = BASE_DIR / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stream = open(logs / "backend.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - process lifetime
    sys.stdout = sys.stderr = stream


def _open_running(argv_ui: bool) -> bool:
    """Another engine holds the data folder: in browser mode, open its UI instead of failing."""
    if not argv_ui:
        return False
    from backend import tray

    try:
        url = tray.url_file().read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not url:
        return False
    print(f"[backend] CatCrewAI is already running: opening {url}", flush=True)
    webbrowser.open(url)
    return True


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.tray:
        args.ui = True
        if sys.stdout is None or os.path.basename(sys.executable).lower().startswith("pythonw"):
            _log_to_file()
    # Any network address means remote. So does --remote: behind a TLS proxy the server listens on
    # loopback, yet every request comes from the internet.
    remote = args.remote or args.host not in _LOOPBACK
    ui_dir = None
    if args.ui:
        from backend.api import ui

        FRONTEND_DIST = ui.FRONTEND_DIST
        if not ui.ui_ready():
            print(
                f"[backend] browser mode needs the built front end in {FRONTEND_DIST}: "
                "run `cd frontend && npm install && npm run build` first.",
                file=sys.stderr,
            )
            return 2
        ui_dir = FRONTEND_DIST
        explicit = any(a == "--port" or a.startswith("--port=") for a in (argv if argv is not None else sys.argv[1:]))
        if args.port == 0 or not explicit:
            args.port = free_port(args.host, 0 if args.port == 0 else args.port)

    from backend.services.logutil import install as quiet_logs

    quiet_logs()

    from backend.services.instance_lock import acquire

    if not acquire():
        if _open_running(args.ui):
            return 0
        print(
            "[backend] CatCrewAI backend is already running (the desktop app, or another --ui). "
            "Close it first; one engine owns the data folder.",
            file=sys.stderr,
        )
        return 1

    import uvicorn

    from backend.api.app import create_app

    app = create_app(ui_dir=ui_dir, remote=remote)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        proxy_headers=True,  # X-Forwarded-Proto from a TLS proxy on this machine: Secure cookies
        log_level=args.log_level,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        # Sockets stay open across long agent turns; never drop them for being idle.
        timeout_keep_alive=75,
    )
    server = uvicorn.Server(config)
    app.state.server = server

    result = {"code": 0}
    if args.smoke:

        def _probe() -> None:
            import json
            import urllib.request

            url = f"http://{args.host}:{args.port}/api/health"
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.1)
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - loopback
                    ok = resp.status == 200 and json.loads(resp.read()).get("status") == "ok"
                result["code"] = 0 if ok else 1
                print(f"[backend] smoke: {'ok' if ok else 'FAILED'}", flush=True)
            except Exception as exc:  # noqa: BLE001
                result["code"] = 1
                print(f"[backend] smoke failed: {exc}", flush=True)
            server.should_exit = True

        threading.Thread(target=_probe, daemon=True).start()

    # Tauri (or a script) waits for this line before it connects.
    print(f"CATCREW_LISTENING http://{args.host}:{args.port}", flush=True)
    if remote:
        from backend.services import auth

        print(f"[backend] REMOTE MODE on {args.host}:{args.port}: every request needs a login.", flush=True)
        print("[backend] Serve it over HTTPS (a TLS proxy such as Caddy in front), never plain HTTP on the internet.", flush=True)
        if auth.needs_setup():
            print(f"[backend] No account yet. Setup code for the first (admin) account: {auth.issue_setup_code()}", flush=True)
    url = ""
    if ui_dir is not None:
        from backend import tray

        shown = "127.0.0.1" if args.host == "localhost" else f"[{args.host}]" if ":" in args.host else args.host
        url = f"http://{shown}:{args.port}/"
        print(f"[backend] CatCrewAI is open at {url}", flush=True)
        try:
            tray.url_file().parent.mkdir(parents=True, exist_ok=True)
            tray.url_file().write_text(url, encoding="utf-8")
        except OSError:
            pass
        if not args.no_browser and not args.smoke:
            threading.Thread(target=_open_browser, args=(server, url), daemon=True).start()
    try:
        if args.tray and not args.smoke:
            _run_with_tray(server, url)
        else:
            server.run()
    finally:
        if url:
            from backend import tray

            try:
                tray.url_file().unlink()
            except OSError:
                pass
    return result["code"]


def _run_with_tray(server, url: str) -> None:
    """The engine on a worker thread, the tray icon on the main thread (macOS needs it there)."""
    from backend import tray

    missing = tray.available()
    if missing:
        print(f"[backend] {missing}", flush=True)
        server.run()
        return
    engine = threading.Thread(target=server.run, name="catcrew-engine")
    engine.start()

    def stop() -> None:
        server.should_exit = True

    try:
        tray.run(url, stop_engine=stop, engine_alive=engine.is_alive)
    finally:
        server.should_exit = True
        engine.join(timeout=30)


if __name__ == "__main__":
    sys.exit(main())
