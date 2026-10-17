"""Run eval scenarios through the real harness and measure them.

Two modes, both on the real tool executors bound to a fresh temp workspace, the tool allow-list,
the approval gate, the loop guards and compaction:

* ``specialist`` (default): ``platform/runner.run_bot`` -- exactly how a cat (Pulse, a developer)
  runs a work order in a project: its persona, the non-negotiable rules, the project context and
  its role's tool pack. This is the product's main path.
* ``chat``: ``run_agent`` with the generic developer prompt plus the turn note -- the no-project
  developer chat (CatCrewAI working on itself), which primes the model toward its own source. What it reports comes from the
harness's own trace (``model_call`` / ``tool_call`` / ``tool`` events), so the eval measures the
same numbers a real session would show.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from backend.evals.scenarios import SCENARIOS, Outcome, Scenario
from backend.providers.base import BaseProvider


@dataclass
class Result:
    scenario: str
    passed: bool
    why: str
    rounds: int = 0
    tool_calls: int = 0
    failed_tools: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    tools: list[str] = field(default_factory=list)
    reply: str = ""
    error: str = ""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metrics(events: list[dict[str, Any]], result: Result) -> None:
    from backend.harness.tool_result import tool_failed

    for event in events:
        kind = event.get("kind")
        if kind == "model_call":
            result.rounds += 1
            result.prompt_tokens += _int(event.get("prompt_tokens"))
            result.cached_tokens += _int(event.get("cached_tokens"))
            result.completion_tokens += _int(event.get("completion_tokens"))
        # The trace stores non-string values as JSON text, so a skip flag reads back as "false".
        elif kind == "tool" and str(event.get("skipped")).lower() not in {"true", "1"}:
            name = str(event.get("name") or "")
            result.tool_calls += 1
            result.tools.append(name)
            if tool_failed(name, str(event.get("result") or "")):
                result.failed_tools += 1


def run_scenario(
    provider: BaseProvider,
    scenario: Scenario,
    *,
    keep: Path | None = None,
    on_tools: Callable[[list[str]], None] | None = None,
    mode: str = "specialist",
) -> Result:
    """One scenario, one fresh workspace. ``keep`` copies the workspace there afterwards;
    ``on_tools`` hears each batch of tool names as the model asks for them (live progress)."""
    from backend.harness.turn_note import attach_turn_note
    from backend.platform.chief import turn_context
    from backend.services import trace
    from backend.services.workspace import current_folders, set_folders
    from backend.tools import build_system_prompt, reset_tool_allowlist, set_tool_allowlist, tools_schema_for

    root = Path(tempfile.mkdtemp(prefix=f"catcrew-eval-{scenario.id}-"))
    workspace = root / "ws"
    workspace.mkdir()
    for rel, body in scenario.files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    session = f"eval-{scenario.id}-{uuid.uuid4().hex[:6]}"
    saved_traces, saved_folders = trace.TRACES_DIR, list(current_folders())
    trace.TRACES_DIR = root / "traces"
    set_folders([workspace])
    names = set(scenario.tools)
    schema = [item for item in tools_schema_for("developer") if (item.get("function") or {}).get("name") in names]
    allow = set_tool_allowlist(frozenset(names))
    trace.bind_session(session)
    result = Result(scenario=scenario.id, passed=False, why="")
    started = time.monotonic()
    try:
        from backend.tools import execute_tool

        messages = attach_turn_note(
            [
                {"role": "system", "content": build_system_prompt("developer")},
                {"role": "user", "content": scenario.task},
            ],
            turn_context(),
        )
        try:
            if mode == "specialist":
                result.reply = _run_specialist(provider, scenario, workspace, root, on_tools)
            else:
                result.reply = (
                    provider.run_agent(messages, schema, execute_tool, max_rounds=scenario.max_rounds, on_tools=on_tools)
                    or ""
                )
        except Exception as exc:  # noqa: BLE001 - a crashed run is a failed scenario, not a crashed eval
            result.error = f"{type(exc).__name__}: {exc}"
        result.seconds = round(time.monotonic() - started, 1)
        _metrics(trace.load_events(session), result)
        if result.error:
            result.why = f"run crashed: {result.error}"
        else:
            try:
                result.passed, result.why = scenario.check(Outcome(workspace, result.reply, list(result.tools)))
            except Exception as exc:  # noqa: BLE001
                result.passed, result.why = False, f"check crashed: {exc}"
    finally:
        trace.bind_session("")
        reset_tool_allowlist(allow)
        set_folders(saved_folders)
        trace.TRACES_DIR = saved_traces
        if keep is not None:
            shutil.copytree(root, keep / f"{scenario.id}-{session[-6:]}", dirs_exist_ok=True)
        shutil.rmtree(root, ignore_errors=True)
    return result


class _LiveLines(list):
    """``run_bot`` reports each tool call by appending a line to a list; this one also prints it."""

    def __init__(self, on_line: Callable[[list[str]], None] | None) -> None:
        super().__init__()
        self._on_line = on_line

    def append(self, line: str) -> None:  # type: ignore[override]
        super().append(line)
        if self._on_line is not None:
            self._on_line([str(line)])


def _run_specialist(
    provider: BaseProvider,
    scenario: Scenario,
    workspace: Path,
    root: Path,
    on_tools: Callable[[list[str]], None] | None,
) -> str:
    """The scenario as a work order for a developer cat in a throw-away project. Its persona is the
    installed Pulse's when there is one (the prompt the Agents page edits), else a plain developer.
    The operations ledger is pointed at a temp file for the run: nothing lands in catcrew.db."""
    from backend.db import ledger
    from backend.platform.models import Bot, Project, Registry
    from backend.platform.runner import run_bot

    try:
        from backend.platform.registry import load_registry

        pulse = load_registry().bots.get("pulse")
    except Exception:  # noqa: BLE001 - no catalog: a plain developer will do
        pulse = None
    bot = Bot(
        id="eval-developer",
        name=getattr(pulse, "name", "") or "Pulse",
        role="developer",
        system_prompt=getattr(pulse, "system_prompt", "") or "You are Pulse, the developer. Build what the task asks.",
    )
    project = Project(id="eval-project", name="Eval project", folders=[str(workspace)])
    registry = Registry(bots={bot.id: bot}, projects={project.id: project})
    saved_db, saved_init = ledger.DB_PATH, ledger._initialized
    ledger.DB_PATH, ledger._initialized = root / "ledger.db", False
    try:
        return run_bot(
            bot,
            scenario.task,
            project=project,
            registry=registry,
            max_rounds=scenario.max_rounds,
            provider=provider,
            abort=lambda: False,
            auto_build_bridge=False,
            collect_tool_events=_LiveLines(on_tools),
        ) or ""
    finally:
        ledger.DB_PATH, ledger._initialized = saved_db, saved_init


def run_all(
    provider: BaseProvider,
    ids: list[str] | None = None,
    *,
    repeat: int = 1,
    keep: Path | None = None,
    progress: Callable[[str], None] | None = None,
    mode: str = "specialist",
) -> Iterator[Result]:
    """Every chosen scenario ``repeat`` times, yielding each result as soon as it is in, so a
    caller can report progress and keep what finished if the run is stopped half-way."""
    chosen = [SCENARIOS[i] for i in (ids or list(SCENARIOS))]
    plan = [scenario for scenario in chosen for _ in range(max(1, repeat))]
    say = progress or (lambda _line: None)
    for index, scenario in enumerate(plan, start=1):
        tag = f"[{index}/{len(plan)} {scenario.id}]"
        say(f"{tag} {scenario.title}")
        started = time.monotonic()
        state = {"round": 0}

        def _tools(names: list[str], tag: str = tag, started: float = started, state: dict = state) -> None:
            state["round"] += 1
            say(f"{tag}   round {state['round']} · {time.monotonic() - started:.0f}s · {', '.join(names)}")

        result = run_scenario(provider, scenario, keep=keep, on_tools=_tools, mode=mode)
        say(
            f"{tag} {'PASS' if result.passed else 'FAIL'} in {result.seconds}s, {result.rounds} rounds, "
            f"{result.tool_calls} tools: {result.why}"
        )
        yield result


def summary(results: list[Result]) -> dict[str, Any]:
    n = len(results) or 1
    return {
        "runs": len(results),
        "pass_rate": round(sum(r.passed for r in results) / n, 3),
        "avg_rounds": round(sum(r.rounds for r in results) / n, 1),
        "avg_tool_calls": round(sum(r.tool_calls for r in results) / n, 1),
        "failed_tools": sum(r.failed_tools for r in results),
        "prompt_tokens": sum(r.prompt_tokens for r in results),
        "cached_tokens": sum(r.cached_tokens for r in results),
        "completion_tokens": sum(r.completion_tokens for r in results),
        "seconds": round(sum(r.seconds for r in results), 1),
    }


def to_json(results: list[Result], meta: dict[str, Any]) -> str:
    return json.dumps(
        {"meta": meta, "summary": summary(results), "results": [asdict(r) for r in results]},
        ensure_ascii=False,
        indent=2,
    )


def table(results: list[Result]) -> str:
    rows = [f"{'scenario':<11} {'ok':<4} {'rounds':>6} {'tools':>5} {'fail':>4} {'in':>8} {'cached':>8} {'out':>6} {'s':>6}  why"]
    for r in results:
        rows.append(
            f"{r.scenario:<11} {'PASS' if r.passed else 'FAIL':<4} {r.rounds:>6} {r.tool_calls:>5} {r.failed_tools:>4} "
            f"{r.prompt_tokens:>8} {r.cached_tokens:>8} {r.completion_tokens:>6} {r.seconds:>6}  {r.why}"
        )
    s = summary(results)
    rows.append(
        f"\n{s['runs']} runs · pass rate {s['pass_rate']:.0%} · {s['avg_rounds']} rounds and "
        f"{s['avg_tool_calls']} tool calls on average · {s['prompt_tokens']} prompt tokens "
        f"({s['cached_tokens']} cached) · {s['completion_tokens']} out · {s['seconds']}s"
    )
    return "\n".join(rows)


def compare(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Side by side summaries of two saved runs (``--compare old.json``)."""
    a, b = before.get("summary") or {}, after.get("summary") or {}
    lines = [f"{'metric':<18} {'before':>12} {'after':>12}"]
    for key in ("runs", "pass_rate", "avg_rounds", "avg_tool_calls", "failed_tools", "prompt_tokens", "cached_tokens", "completion_tokens", "seconds"):
        lines.append(f"{key:<18} {a.get(key, '-')!s:>12} {b.get(key, '-')!s:>12}")
    return "\n".join(lines)
