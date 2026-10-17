"""``python -m backend`` — run the API server on loopback.

    python -m backend                 # http://127.0.0.1:8765
    python -m backend --port 9000
    python -m backend --smoke         # start, check /api/health, exit 0/1

Only loopback binds are accepted: the API can run shell commands and edit files
on the user's machine, so it must not be reachable from the network.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

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
    parser.add_argument("--log-level", default="warning")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.host not in _LOOPBACK:
        print(f"[backend] refusing to bind {args.host!r}: the API is loopback-only.", file=sys.stderr)
        return 2

    from backend.services.logutil import install as quiet_logs

    quiet_logs()

    from backend.services.instance_lock import acquire

    if not acquire():
        print("[backend] CatCrewAI backend is already running.", file=sys.stderr)
        return 1

    import uvicorn

    from backend.api.app import create_app

    app = create_app()
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
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
    server.run()
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())
