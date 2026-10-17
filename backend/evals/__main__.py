r"""Run the harness evals against a real model.

    python -m backend.evals                          # every scenario, the Coding model from Models
    python -m backend.evals fix-bug rename --repeat 3
    python -m backend.evals --host openrouter --model anthropic/claude-sonnet-4
    python -m backend.evals --compare data/evals/20261005-221500.json

Results are printed as a table and saved to data/evals/<timestamp>.json; ``--compare`` prints the
summary of a saved run next to this one, so a harness change can be judged by pass rate, rounds and
tokens instead of by feel. Tool approvals are not asked during an eval (there is no one to ask).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from backend.config import BASE_DIR
from backend.evals.run import compare, run_all, table, to_json
from backend.evals.scenarios import SCENARIOS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.evals", description=__doc__.split("\n\n")[0])
    parser.add_argument("scenarios", nargs="*", help=f"scenario ids (default: all of {', '.join(SCENARIOS)})")
    parser.add_argument("--host", default="", help="model host (default: the Coding role on the Models page)")
    parser.add_argument("--model", default="", help="model name for --host")
    parser.add_argument("--repeat", type=int, default=1, help="runs per scenario (models are not deterministic)")
    parser.add_argument("--compare", type=Path, help="a saved result file to compare this run with")
    parser.add_argument("--keep", action="store_true", help="keep each workspace and trace under data/evals/workspaces")
    parser.add_argument("--no-jev", action="store_true", help="run with the Jev gatekeeper off (to measure what it adds)")
    parser.add_argument(
        "--mode",
        choices=("specialist", "chat"),
        default="specialist",
        help="specialist: a developer cat's work order in a project (default); chat: the no-project developer chat",
    )
    args = parser.parse_args(argv)

    unknown = [s for s in args.scenarios if s not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario(s): {', '.join(unknown)}")

    from backend.core.provider_factory import AGENT_ROLE, provider_for_role, provider_from_brain
    from backend.harness.approvals import reset_approval_hook, set_approval_hook
    from backend.harness.runtime import reset_provider, set_provider

    provider = provider_from_brain(args.host, args.model) if args.host else provider_for_role(AGENT_ROLE)
    from backend.middleware import jev_gatekeeper

    if args.no_jev:
        jev_gatekeeper.enabled = lambda: False  # this process only; the saved setting is untouched
    jev_on = jev_gatekeeper.enabled()
    label = f"{getattr(provider, 'host_id', '') or args.host or 'host'}/{getattr(provider, 'model', '') or args.model}"
    print(
        f"[eval] {label} · {len(args.scenarios or SCENARIOS)} scenario(s) × {args.repeat} · {args.mode} mode · Jev {'on' if jev_on else 'off'}",
        flush=True,
    )

    out_dir = BASE_DIR / "data" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = out_dir / "workspaces" if args.keep else None
    provider_token = set_provider(provider)
    hook_token = set_approval_hook(lambda _name, _args: "allow")
    results = []
    stopped = False
    try:
        for result in run_all(
            provider,
            args.scenarios or None,
            repeat=args.repeat,
            keep=keep,
            progress=lambda line: print(line, flush=True),
            mode=args.mode,
        ):
            results.append(result)
    except KeyboardInterrupt:
        # Ctrl+C: keep and save what finished; the scenario that was running is dropped.
        stopped = True
        print("\n[eval] stopped; reporting the runs that finished", flush=True)
    finally:
        reset_approval_hook(hook_token)
        reset_provider(provider_token)
    if not results:
        print("[eval] no run finished")
        return 130 if stopped else 1

    print("\n" + table(results))
    meta = {"model": label, "at": datetime.now().isoformat(timespec="seconds"), "repeat": args.repeat, "stopped": stopped, "jev": jev_on, "mode": args.mode}
    saved = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    saved.write_text(to_json(results, meta), encoding="utf-8")
    print(f"\n[eval] saved {saved}")
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        print("\n" + compare(before, json.loads(saved.read_text(encoding="utf-8"))))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
