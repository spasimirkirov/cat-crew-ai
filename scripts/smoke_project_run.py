r"""Real-model smoke run: send a few short messages to Cat Crew and report what the cats did.

    .\.venv\Scripts\python.exe scripts\smoke_project_run.py
    .\.venv\Scripts\python.exe scripts\smoke_project_run.py --steps design --project "Cat Crew AI Website"

It starts its own engine on a spare port with the code in this folder, so close the CatCrewAI
window first (one engine per data folder). Tool approvals are answered "allow" automatically.
For each step it posts one message to Cat Crew, waits for the turn to finish and prints a short
report: who answered, how many steps, and any of the failure markers below. The full log goes
to data/smoke/<timestamp>.log.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS = {
    "design": (
        "@Pixel Finish the visual design of the landing page: shared glassmorphism tokens in "
        "src/index.css, fix any dead buttons or anchor links, consistent Section/Card spacing, "
        "responsive breakpoints, Tailwind classes instead of inline styles. Run npm run build to "
        "check it compiles, then report what you changed."
    ),
    "qa": (
        "@Aegis Verify the landing page with real evidence: npm run build exits 0, the page "
        "renders, icons and anchors work, no console errors (serve it and use browser_check). "
        "Start your reply with PASS or FAIL."
    ),
}
MARKERS = ("got stuck", "is not available", "Skipped:", "Denied by user", "Error:")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="Cat Crew AI Website")
    ap.add_argument("--steps", default="design,qa")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--timeout-min", type=int, default=20)
    ap.add_argument(
        "--attach",
        action="store_true",
        help="do not start an engine: use the one already running on --port (8765 for the desktop app). "
        "It runs whatever code it was started with, so restart it first if you changed the backend.",
    )
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (ROOT / "data" / "smoke").mkdir(parents=True, exist_ok=True)
    engine_log = ROOT / "data" / "smoke" / f"{stamp}-engine.log"
    base = f"http://127.0.0.1:{args.port}"

    def call(method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    out = open(engine_log, "w", encoding="utf-8")
    if args.attach:
        if args.port == 8799:
            args.port = 8765
            base = f"http://127.0.0.1:{args.port}"
        engine = None
        print(f"Attaching to the engine on port {args.port} (not starting one).")
    else:
        engine = subprocess.Popen(
            [sys.executable, "-m", "backend", "--port", str(args.port)],
            cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
        )
    try:
        for _ in range(90):
            if engine is not None and engine.poll() is not None:
                out.flush()
                lines = engine_log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
                print("Engine exited early. Last lines:")
                print("\n".join(lines))
                if any("already running" in line for line in lines):
                    print(
                        "\nAnother CatCrewAI backend holds data\\backend.lock (the desktop app, or a backend left "
                        "behind after it was closed). Find it with:\n"
                        "  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'backend|CatCrewAI' } "
                        "| Select-Object ProcessId, Name, CommandLine\n"
                        "close that process (Stop-Process -Id <ProcessId>), then run this script again. "
                        "Or add --attach to use the running engine (restart it first if it predates your changes)."
                    )
                return 2
            try:
                call("GET", "/api/health")
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1)
        else:
            print("Engine did not answer /api/health in 90 s.")
            return 2

        projects = call("GET", "/api/projects")
        project = next((p for p in projects if p.get("name") == args.project), None)
        if project is None:
            print("No project named", args.project, "- have:", [p.get("name") for p in projects])
            return 2
        pid = project["id"]
        print(f"Project {project['name']} ({pid})")

        approved: list[str] = []

        def approver() -> None:
            try:
                from websockets.sync.client import connect
            except ImportError:
                print("websockets missing: approvals will not be answered")
                return
            while engine is None or engine.poll() is None:
                try:
                    with connect(f"ws://127.0.0.1:{args.port}/ws/events?project_id={pid}") as ws:
                        for frame in ws:
                            event = json.loads(frame)
                            if event.get("type") == "approval.requested":
                                data = event.get("data") or {}
                                aid = data.get("approval_id") or data.get("id")
                                if aid:
                                    call("POST", f"/api/approvals/{aid}", {"decision": "allow"})
                                    approved.append(str(data.get("tool") or data.get("name") or "?"))
                except Exception:  # noqa: BLE001 - reconnect until the engine stops
                    time.sleep(1)

        threading.Thread(target=approver, daemon=True).start()

        problems = 0
        for name in [s.strip() for s in args.steps.split(",") if s.strip()]:
            text = STEPS.get(name, name)
            before = len(call("GET", f"/api/projects/{pid}/threads/crew/messages?limit=2000"))
            t0 = time.time()
            call("POST", f"/api/projects/{pid}/threads/crew/messages", {"text": text})
            print(f"\n== {name}: sent, waiting (up to {args.timeout_min} min)")
            idle = 0
            last_report = 0.0
            while time.time() - t0 < args.timeout_min * 60:
                time.sleep(3)
                idle = idle + 1 if not call("GET", "/api/turns/current") else 0
                if idle >= 3:
                    break
                if time.time() - last_report > 60:
                    last_report = time.time()
                    print(f"   ...{int(time.time() - t0)} s, {len(approved)} approvals")
            else:
                print("   timed out; the turn is still running")
            new = call("GET", f"/api/projects/{pid}/threads/crew/messages?limit=2000")[before:]
            for m in new:
                if m.get("role") == "user":
                    continue
                body = str(m.get("text") or "")
                steps = len(((m.get("activity") or {}).get("steps")) or [])
                raw = json.dumps(m, ensure_ascii=False)
                hits = sorted({k for k in MARKERS if k in raw})
                if "got stuck" in raw:
                    problems += 1
                print(f"   [{m.get('speaker') or m.get('role')}] {steps} steps | {body[:140]!r}" + (f" | markers: {hits}" if hits else ""))
            with open(ROOT / "data" / "smoke" / f"{stamp}.log", "a", encoding="utf-8") as fh:
                fh.write(f"## {name}\n" + json.dumps(new, ensure_ascii=False, indent=1) + "\n")
        print(f"\nDone. 'got stuck' turns: {problems}. Approvals answered: {len(approved)}.")
        print("Full log: data/smoke/" + stamp + ".log")
        return 1 if problems else 0
    finally:
        if engine is not None:  # never shut down an engine we did not start
            try:
                call("POST", "/api/shutdown")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
            if engine.poll() is None:
                engine.terminate()
        out.close()


if __name__ == "__main__":
    raise SystemExit(main())
