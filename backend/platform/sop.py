"""Cat Crew SOP engine (Master Architecture Document Phase 3 — the
"kill Chief of Staff" pivot).

Before this module, a project-bound chat ran an LLM "Chief" that decided,
turn by turn, whether to call ``delegate_to_bot``. That created three
concrete problems reported from a live session: Chief is a bottleneck
(nothing happens until it decides to delegate), a delegated bot's tool
calls never reach the UI (they run inside Chief's own opaque tool result),
and dependency roles (QA, DevOps) get skipped whenever Chief forgets to
call them.

This module replaces that judgment call with a deterministic phase state
machine. There is no LLM "should I delegate" turn any more: a message in
the Cat Crew either names a bot with ``@name`` (routed straight to
that bot, bypassing the phase order) or is treated as a new brief that
runs the whole pipeline — Architect -> Lead -> DevOps -> Developer ->
Designer -> QA, with Legal/Accountant spontaneously inserted when the
brief's own language calls for them. Every bot's real output becomes its
own message in the shared feed (via ``on_turn``), not a buried tool
result, and every tool call it makes is collected as a short visible
event line (``run_bot``'s ``collect_tool_events``).

``chat_window.py``'s ``ProjectRoomWorker`` is the caller: it runs this on
a background thread and streams each ``SopTurn`` back to the UI as it
completes, instead of waiting for the whole pipeline before showing
anything.
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from backend.db import ledger as db
from .chief import _format_delegation_result
from .delegation import run_delegated
from .models import Bot, Project, Registry
from .registry import save_registry, staff_bot_on_project
from ..providers.base import hit_round_cap, hit_stall

_MENTION = re.compile(r"@(\w+)")

# (phase label, role) in strict pipeline order. One bot per role runs each
# phase; a role with nobody assigned is skipped rather than blocking.
#
# CatCrewAI rebrand: "blueprint" is new (Cipher turns the raw brief into a
# structured task blueprint before anyone else starts); "architecture" and
# "distribution" now both run under the single "vanguard" role, which
# merges the old separate Architect + Lead bots into one Team Lead &
# Architect persona (system design AND task delegation).
PHASE_ROLES: tuple[tuple[str, str], ...] = (
    ("blueprint", "prompt_engineer"),
    ("architecture", "vanguard"),
    ("distribution", "vanguard"),
    ("devops", "devops"),
    ("build", "developer"),
    ("design", "designer"),
    ("qa", "qa"),
)

# Roles whose job is to produce something on disk (write files / run real
# commands via runner.py's tool loop). A turn from one of these roles that
# made zero tool calls did not actually build anything — see _run_one's
# `verified` flag ("no fake handoffs", CrewAI/AutoGen "strict execution
# verification" principle). Advisory-only roles (prompt_engineer, legal,
# accountant, marketing, chief) are expected to just talk, so they are
# always considered verified.
_BUILDER_ROLES = frozenset({"vanguard", "devops", "developer", "designer", "qa"})
# Roles whose phase only counts when something on disk changed: a file written or a command run.
# Reading composer.json three times and stopping is not a build (it was marked "done" anyway, run
# after run, while the site stayed a blank page).
_PRODUCER_ROLES = frozenset({"devops", "developer", "designer"})
_PRODUCER_PHASES = ("devops", "build", "design")
_EXECUTED = re.compile(r" executed ([A-Za-z0-9_]+)")
# The model answered with nothing at all, twice (``providers/base.py`` ``_empty_text``).
_EMPTY_REPLY = "returned an empty reply twice"
# The tool loop's own advice when it runs out of rounds; in the pipeline the harness continues itself.
_SEND_CONTINUE = (
    "The task is not finished: send continue to resume it, or raise the tool-round cap in "
    "Settings → General."
)


# Tool rounds per work order, by phase. Planning is a few reads and one write; a build slice is
# dozens of files. One cap for everything (40) cut the designer and QA off mid-work while it was
# far more than a planner needs. The stall guard below is what stops a loop, not these numbers.
PHASE_BUDGETS: dict[str, int] = {
    "blueprint": 15,
    "architecture": 15,
    "distribution": 15,
    "devops": 40,
    "build": 100,
    "build-item": 60,
    "design": 60,
    "qa": 60,
    "qa-fix": 50,
    "spontaneous": 60,
    "mention": 60,
}
# Producing phases stop after this many tool rounds in a row that changed nothing.
_STALL_ROUNDS = 12
_STALL_PHASES = frozenset({"devops", "build", "design", "qa-fix", "spontaneous", "mention"})
# A work order that ran out of rounds while still making progress is continued, up to this many
# times, without asking anyone to type "continue". The stall guard ends one that stopped progressing.
_MAX_AUTO_CONTINUES = 3
# QA -> fix -> QA, at most this many QA checks per run.
_QA_CYCLES = 3
# "continue", "try again", "довърши" ...: finish the run that is already planned, do not re-plan it.
_CONTINUE_RE = re.compile(
    r"^\W*(?:(?:ok(?:ay)?|добре|хайде|давай|so|now)[\s,!.]*)*"
    r"(?:continue|resume|go on|carry on|keep going|finish(?:\s+it)?|try\s+(?:again|now)|retry|"
    r"продължи|продължавай|довърши|пробвай|опитай)",
    re.IGNORECASE,
)


def _changed_something(tool_events: list[str]) -> bool:
    """True when a tool that changes files or runs something was called (``harness/tool_meta``)."""
    from backend.harness import tool_meta

    for line in tool_events:
        match = _EXECUTED.search(line)
        if match and tool_meta.meta(match.group(1)).kind in (tool_meta.ACT, tool_meta.CONTAINED):
            return True
    return False

# Keyword sniffing for Phase 5 "spontaneous inclusion" — deliberately
# simple (see master architecture note): a real intent classifier is a
# follow-up, not a blocker for shipping the pipeline itself.
_LEGAL_KEYWORDS = (
    "legal",
    "contract",
    "terms of service",
    "terms & conditions",
    "privacy policy",
    "gdpr",
    "compliance",
    "eula",
    "liability",
    "trademark",
    "copyright",
)
_BUDGET_KEYWORDS = (
    "budget",
    "pricing",
    "price",
    "invoice",
    "cost",
    " fee",
    "payment",
    "revenue",
    "subscription plan",
    "ledger",
)


@dataclass
class SopTurn:
    """One bot's contribution to the Cat Crew feed."""

    bot: Bot
    text: str
    tool_events: list[str] = field(default_factory=list)
    phase: str = ""
    verified: bool = True
    task_id: str = ""
    # The cat ran out of its tool-round budget (``delegate_round_budget``): the work order
    # ends ``blocked``, not ``done``, and the reply says so.
    round_capped: bool = False
    # The run itself failed (the model was turned away, the host errored): not done, whatever the role.
    failed: bool = False

    @property
    def ledger_status(self) -> str:
        if self.failed:
            return "failed"
        if self.round_capped:
            return "blocked"
        return "done" if self.verified else "unverified"


# What a later specialist is told about earlier phases. The full text used to be accumulated
# and handed on verbatim (it lands in the specialist's system prompt, which compaction cannot
# shrink), so by QA the prompt carried every earlier phase. Now each phase contributes a short
# excerpt, the total is capped, and the details live in files the cat can read on demand.
_PRIOR_SECTION_CAP = 1_200
_PRIOR_TOTAL_CAP = 4_500
_HANDOFF_NOTE = (
    "Only short excerpts of earlier phases are shown above. The full outputs are files in the "
    "project folder (BLUEPRINT.md, ARCHITECTURE.md, TASKS.md, BUILD_STATUS.md, "
    "PROJECT_STRUCTURE.md, when they exist): read_file one only if you need the detail."
)


def _append_prior(prior: str, text: str) -> str:
    """Add one phase's output to the hand-off: excerpted, and the oldest part dropped first."""
    section = (text or "").strip()
    # The runner appends a directory listing after every turn; it is noise for the next cat.
    section = section.split("\n\nComputer after this turn:", 1)[0].strip()
    if len(section) > _PRIOR_SECTION_CAP:
        section = section[:_PRIOR_SECTION_CAP].rstrip() + " …[excerpt]"
    joined = (prior.strip() + "\n\n" + section).strip() if prior.strip() else section
    if len(joined) > _PRIOR_TOTAL_CAP:
        joined = "…" + joined[-_PRIOR_TOTAL_CAP:]
    return joined


def parse_mention(text: str, registry: Registry) -> Bot | None:
    """First ``@name`` in ``text`` that matches a known bot id or name."""
    for raw in _MENTION.findall(text or ""):
        needle = raw.strip().lower()
        if not needle:
            continue
        for bot in registry.bots.values():
            if bot.id.lower() == needle or bot.name.lower() == needle:
                return bot
    return None


def _qa_verdict_body(text: str) -> str:
    """What the QA cat itself said, without the wrappers the engine puts around a turn.

    A delegated turn comes back as ``=== QA VERDICT — Name ===`` / ``[Name / role]`` / the reply /
    ``Computer after this turn: ...`` / ``=== END QA VERDICT ===``. Only the reply says PASS or FAIL.
    The header line used to be left in place, so a FAIL was never seen and no QA rejection ever
    reached the developer."""
    body = text or ""
    if "=== QA VERDICT" in body:
        _banner, _sep, body = body.partition("\n")
        body = body.split("=== END QA VERDICT ===")[0]
    body = body.lstrip()
    if body.startswith("[") and "\n" in body:
        header, _sep, rest = body.partition("\n")
        if header.rstrip().endswith("]") and " / " in header:
            body = rest
    return body.split("\n\nComputer after this turn:", 1)[0].strip()


def _qa_verdict_failed(text: str) -> bool:
    """True when a QA turn's own reply opens with a FAIL verdict (or it never finished)."""
    body = _qa_verdict_body(text)
    # A QA turn that ended stuck on a repeating tool (the loop guard's message) verified nothing:
    # it must not read as a pass. The stuck text names the tool and its error, which is exactly
    # what the developer needs to see.
    if re.match(r"[\s*_#>`-]*(?:verdict\s*[:\-—]\s*[\s*_]*)?(FAIL\b|I got stuck)", body, re.IGNORECASE):
        return True
    # No verdict at all (an empty reply, leaked tool-call tokens, a chat about the weather) is not a
    # pass either: only a reply that says PASS somewhere in its opening lines lets the run end green.
    opening = "\n".join(body.splitlines()[:6])
    return not re.search(r"\bPASS(?:ED)?\b", opening)


def _bot_for_role(registry: Registry, project: Project, role: str) -> Bot | None:
    role = role.lower()
    on_project = [b for b in registry.project_bots(project) if (b.role or "").lower() == role]
    if on_project:
        return on_project[0]
    return next((b for b in registry.bots.values() if (b.role or "").lower() == role), None)


def _spontaneous_bots(registry: Registry, project: Project, brief: str) -> list[Bot]:
    blob = (brief or "").lower()
    extra: list[Bot] = []
    if any(word in blob for word in _LEGAL_KEYWORDS):
        bot = _bot_for_role(registry, project, "legal")
        if bot:
            extra.append(bot)
    if any(word in blob for word in _BUDGET_KEYWORDS):
        bot = _bot_for_role(registry, project, "accountant")
        if bot:
            extra.append(bot)
    return extra


def _phase_task(phase: str, brief: str, *, task_id: str = "") -> str:
    brief = (brief or "").strip()
    text = _phase_task_body(phase, brief)
    if task_id:
        text += f"\n\nYour task_id for report_task_results is: {task_id}"
    return text


def _phase_task_body(phase: str, brief: str) -> str:
    if phase == "blueprint":
        return (
            f"Raw user brief:\n{brief}\n\n"
            "Turn this into a structured task blueprint for the team: goals, "
            "constraints, target stack, and what each role (Vanguard, DevOps, "
            "Developer, Designer, QA) specifically needs to know before starting. "
            "Do not design the system yourself — that is Vanguard's job next."
        )
    if phase == "architecture":
        return (
            f"New project brief:\n{brief}\n\n"
            "Analyze this and produce a complete file tree and tech stack plan."
        )
    if phase == "distribution":
        return (
            f"Original brief:\n{brief}\n\n"
            "The architecture plan is above in context. Break it into a strict todo "
            "list by role (DevOps, Developer, Designer, QA) and post it here so the "
            "team can follow it. End your reply with these two sections, exactly in this "
            "format (the harness reads them):\n\n"
            "## Work orders\n"
            "1. <one buildable slice of the product, e.g. Layout, navigation and home page "
            "sections> -- <the files it touches>\n"
            "(3 to 8 orders in build order; each one finished and testable on its own; the "
            "developer builds them one at a time)\n\n"
            "## Acceptance checks\n"
            '- GET /path -> 200, shows "exact text that must be on the page"\n'
            "- GET /admin -> redirects to login\n"
            "(one line per page or protected route in the brief, using real texts from the "
            "brief; the harness requests each URL and checks the texts)"
        )
    if phase == "devops":
        return (
            f"Original brief:\n{brief}\n\n"
            "Create and prepare the project before the developer builds. If the stack "
            "is a framework with an official skeleton (Laravel, Vite, Next.js...), create "
            "it with scaffold_project and the framework's non-interactive generator, "
            "target folder as {dir} (it works in a folder that already has files); a "
            "hand-written composer.json is not a Laravel project. For a plain stack, "
            "write package.json / requirements.txt yourself. If the folder already holds the "
            "framework, do not scaffold again: read its manifest and note the exact framework "
            "version (php artisan --version), and only add packages that support that version "
            "(look it up in the official docs with web_search / web_fetch when unsure; for "
            "example Jetstream and Breeze do not support every Laravel release). Then set up "
            "the environment end to end: .env (APP_URL, database), the database itself, "
            "migrations, storage link, install dependencies, build the assets "
            "(npm run build), and clear the caches. Verify with the framework's own command "
            "and paste its output in your reply."
        )
    if phase == "build":
        return (
            f"Original brief:\n{brief}\n\n"
            "First confirm DevOps's manifest (package.json/composer.json/"
            "requirements.txt) is actually on disk via list_dir — if it is not, stop "
            "and say this must go back to DevOps instead of scaffolding it yourself. "
            "Once confirmed, build EVERY feature in the brief on disk with write_file / "
            "replace_in_file: models, migrations, seeders, controllers, routes, views, forms, the "
            "admin area. Reading files is not building; do not stop after looking around and "
            "do not ask for permission -- the brief is the permission. Content the brief "
            "lists (sections, services, texts) goes into the pages verbatim. Then run the "
            "migrations and seeders, build the assets, clear the caches, and request each "
            "page once to prove it renders. Finish with the list of files you wrote, the "
            "commands you ran, and the features you added (QA writes automated tests for them)."
        )
    if phase == "design":
        return (
            f"Original brief:\n{brief}\n\n"
            "The developer's base implementation is above in context — confirm it's "
            "actually on disk via list_dir before styling anything; if it isn't, say "
            "so instead of scaffolding it yourself. Apply visual styling, CSS, and "
            "layout hierarchy per the Lead's todo list by editing the real view and style "
            "files, then rebuild the assets. Finish with the files you changed."
        )
    if phase == "qa":
        return (
            f"Original brief:\n{brief}\n\n"
            "Prove the product works -- with automated tests, not by walking every page by "
            "hand each time (read_skill automated-testing for the patterns of each stack):\n"
            "1. Run the project's test suite first (Laravel: php artisan test; JavaScript: the "
            "package.json test script or npx vitest run; Python: pytest).\n"
            "2. Write automated tests for every feature in the brief and every new or changed "
            "feature the developer reported above that has no test yet (for a Laravel site: "
            "feature tests in tests/Feature -- each page renders with its key content, admin "
            "routes send guests to the login, the seeded admin can log in, and the admin "
            "actions work, uploads with Storage::fake). The tests stay in the project and are "
            "re-run on every later check, so the next QA pass takes seconds.\n"
            "3. Run the suite again. A failing test is a FAIL, unless the test itself is wrong: "
            "then fix the test and say so.\n"
            "4. Use the browser only for what tests cannot see: browser_check each public page "
            "once on the real URL (APP_URL in .env, never a guessed port) for console errors, "
            "and one look at mobile width. Do not click through every page by hand.\n"
            "Start your reply with PASS or FAIL. Then list: the tests (how many passed and "
            "failed), the harness smoke checks if any are below, and on FAIL each failing item "
            "with the file or URL involved."
        )
    return brief


# After a cat changed files, the harness checks the project (harness/project_check.py) and hands
# a failure back to the same cat this many times before the work order counts as broken.
_CHECK_FIXES = 2
_CHECK_FIX_ROUNDS = 30


def _project_check(project: Project):
    folder = (project.folders or [""])[0]
    if not folder:
        return None
    try:
        from backend.harness.project_check import check_project

        pipe = project.state.get("pipeline") if isinstance(project.state.get("pipeline"), dict) else {}
        paths = [str(c.get("path") or "") for c in pipe.get("checks") or [] if isinstance(c, dict) and not c.get("redirect")]
        return check_project(folder, paths=[p for p in paths if p.startswith("/")])
    except Exception as exc:  # noqa: BLE001 - a broken check must never break the run
        print(f"[sop] project check skipped: {exc}", flush=True)
        return None


def _check_and_fix(
    bot: Bot,
    task: str,
    text: str,
    tool_events: list[str],
    *,
    project: Project,
    registry: Registry,
    extra_context: str,
    phase: str,
) -> tuple[str, bool]:
    """Check the project after ``bot`` changed it; on a problem, give it back to the same cat with
    the exact errors. Returns the reply (with the fix runs and the final check appended) and
    whether the project is still broken."""
    for attempt in range(_CHECK_FIXES + 1):
        result = _project_check(project)
        if result is None:
            return text, False
        if result.ok:
            return text + ("\n\n" + result.report() if attempt else ""), False
        if attempt == _CHECK_FIXES:
            return (
                text + f"\n\n⚠ The project is still broken after {bot.name}'s work and "
                f"{_CHECK_FIXES} fix attempts:\n{result.report()}"
            ), True
        fix_task = (
            "The harness checked the project right after your work and it is BROKEN. Fix every "
            "problem below now: open each file named, find the cause (for a Blade view: an "
            "@if / @foreach / @section / @push without its closing directive, or a PHP tag left "
            "open), fix it, and run the check yourself again (php artisan view:cache, php -l, "
            "npm run build, request the page). Do not start other work.\n\n"
            f"{result.report()}\n\nThe work order you were doing:\n{task}"
        )
        more = run_delegated(
            bot,
            fix_task,
            project=project,
            registry=registry,
            extra_context=extra_context,
            max_rounds=_CHECK_FIX_ROUNDS,
            stall_rounds=_STALL_ROUNDS,
            collect_tool_events=tool_events,
            provider=_planning_provider(phase, project.id),
        )
        text += f"\n\n[Harness check found problems; {bot.name} fixed them]\n{result.report()}\n\n{more}"
    return text, True


def _continue_task(task: str, tool_events: list[str]) -> str:
    done = "\n".join(f"- {line}" for line in tool_events[-40:]) or "- (nothing recorded)"
    return (
        "CONTINUE: your previous run on this work order ran out of tool rounds while it was "
        "still making progress. Do not redo finished work: check what is on disk, then pick up "
        "where you stopped and finish.\n\nWhat you already did:\n"
        f"{done}\n\nThe work order:\n{task}"
    )


def _work_order_task(brief: str, orders: list[str], index: int) -> str:
    plan = "\n".join(f"{'->' if i == index else '  '} {i}. {order}" for i, order in enumerate(orders, 1))
    return (
        f"Original brief:\n{brief}\n\n"
        f"Build plan ({len(orders)} work orders; the earlier ones are done and on disk):\n{plan}\n\n"
        f"YOUR WORK ORDER ({index} of {len(orders)}): {orders[index - 1]}\n\n"
        "Build this slice completely on disk with write_file / replace_in_file and run what it "
        "needs (migrations, seeders, the asset build). Do not start the other work orders. "
        "Reading files is not building, and the brief is the permission: do not stop to ask. "
        "Content the brief lists goes into the pages verbatim. Finish with the files you wrote, "
        "the commands you ran, and the features this slice added (QA writes automated tests "
        "for them)."
    )


_HEADING = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")


def _section_lines(text: str, keyword: str) -> list[str]:
    """The lines under every markdown heading that contains ``keyword``."""
    lines: list[str] = []
    inside = False
    for line in (text or "").splitlines():
        match = _HEADING.match(line)
        if match:
            inside = keyword in match.group(1).lower()
            continue
        if inside:
            lines.append(line)
    return lines


def _work_orders(text: str) -> list[str]:
    """The "## Work orders" list Vanguard ends the distribution phase with."""
    items: list[str] = []
    for line in _section_lines(text, "work order"):
        match = re.match(r"\s*(?:\d+[.)]|[-*])\s+(?:\[[ xX]\]\s*)?(.+)", line)
        if match:
            item = match.group(1).strip().strip("*").strip()
            if len(item) > 3:
                items.append(item[:300])
    return items[:8]


def _acceptance_checks(text: str) -> list[dict]:
    """The "## Acceptance checks" lines: ``GET /path -> 200, shows "text"`` or ``-> redirects``."""
    checks: list[dict] = []
    for line in _section_lines(text, "acceptance"):
        match = re.search(r"\bGET\s+(/[^\s,;)`]*)", line)
        if not match:
            continue
        checks.append(
            {
                "path": match.group(1),
                "expect": re.findall(r'"([^"]{2,120})"', line)[:5],
                "redirect": bool(re.search(r"redirect", line, re.IGNORECASE)),
            }
        )
    return checks[:30]


def _app_url(project: Project) -> str:
    """APP_URL from the project's .env (Laravel and most frameworks keep the site address there)."""
    folder = (project.folders or [""])[0]
    if not folder:
        return ""
    try:
        env = (Path(folder) / ".env").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^APP_URL\s*=\s*[\"']?([^\"'\s#]+)", env, re.MULTILINE)
    return match.group(1).rstrip("/") if match else ""


def _smoke_report(project: Project, checks: list[dict]) -> str:
    """Request every acceptance URL and check its texts: the harness's own, model-free check."""
    base = _app_url(project)
    if not base or not checks:
        return ""
    import html

    import requests

    lines: list[str] = []
    failed = 0
    for check in checks:
        path = str(check.get("path") or "/")
        try:
            response = requests.get(base + path, timeout=15, allow_redirects=not check.get("redirect"))
        except requests.RequestException as exc:
            lines.append(f"FAIL GET {path}: not reachable ({type(exc).__name__})")
            failed += 1
            continue
        if check.get("redirect"):
            ok = 300 <= response.status_code < 400
            detail = f"HTTP {response.status_code}" + (f" -> {response.headers.get('location', '')}" if ok else ", expected a redirect")
        else:
            body = html.unescape(response.text).lower()
            missing = [text for text in check.get("expect") or [] if text.lower() not in body]
            ok = response.status_code == 200 and not missing
            detail = f"HTTP {response.status_code}" + (f", missing: {'; '.join(missing)}" if missing else "")
        failed += 0 if ok else 1
        lines.append(f"{'PASS' if ok else 'FAIL'} GET {path}: {detail}")
    return (
        f"Harness smoke checks against {base} ({len(checks) - failed} of {len(checks)} passed; "
        "plain HTTP requests, not logged in):\n" + "\n".join(lines)
    )


def _wants_resume(project: Project, message: str) -> bool:
    """Finish the run already planned for this project instead of planning it again."""
    from . import handoff

    asked = handoff.take_resume(project.id)
    pipe = project.state.get("pipeline")
    if not isinstance(pipe, dict) or not pipe.get("brief") or "distribution" not in (pipe.get("done") or []):
        return False
    text = (message or "").strip()
    return asked or text == str(pipe["brief"]).strip() or (len(text) <= 120 and bool(_CONTINUE_RE.match(text)))


def _redo_note(phase: str) -> str:
    return (
        f"SECOND ATTEMPT at the {phase} phase. Your first attempt changed nothing on disk: no "
        "file was written and no command was run (or you answered with nothing). This time do "
        "the work with your tools -- write the files, run the commands -- and do not end your "
        "turn until it is done.\n\n"
    )


def _direct_task(brief: str, bot: Bot, *, task_id: str = "") -> str:
    """Task text for a role picked by the intent router (not the fixed pipeline):
    do what was asked, nothing more — the phase scaffolding prompts do not apply."""
    text = (
        f"The user asked for this (Cat Crew routed it to you as {bot.role or 'a specialist'}):\n"
        f"{(brief or '').strip()}\n\n"
        "Do exactly what was asked with your own tools, on the real files/commands — no fake "
        "handoffs, and no scaffolding or rebuilding beyond the request. Earlier teammates' "
        "output (if any) is above in context. Confirm in your reply what you actually did."
    )
    if task_id:
        text += f"\n\nYour task_id for report_task_results is: {task_id}"
    return text


# Phases that are step-by-step planning: they run on the Planning model when the
# user picked one (a reasoning model is slow and dear, so nothing else uses it).
_PLANNING_PHASES = frozenset({"architecture", "distribution", "fixplan"})


def _planning_provider(phase: str, project_id: str = ""):
    """The provider a phase's own role picks: Planning for the planning phases, Review for QA.
    ``None`` stays on the default (Coding) model -- also when that role is not set."""
    role = "planning" if phase in _PLANNING_PHASES else "review" if phase == "qa" else ""
    if not role:
        return None
    try:
        from backend.core.provider_factory import provider_if_explicit

        return provider_if_explicit(role, project_id)
    except Exception as exc:  # noqa: BLE001 - a bad role setting must not stop the build
        print(f"[sop] {role.title()} model unavailable, using the default model: {exc}", flush=True)
        return None


def _run_one(
    bot: Bot,
    task: str,
    *,
    project: Project,
    registry: Registry,
    extra_context: str = "",
    phase: str = "",
    task_id: str = "",
    budget: int | None = None,
) -> SopTurn:
    staff_bot_on_project(registry, project, bot)
    tool_events: list[str] = []
    rounds = budget if budget is not None else PHASE_BUDGETS.get(phase, 0)
    stall = _STALL_ROUNDS if phase in _STALL_PHASES else 0
    role = (bot.role or "").strip().lower()
    try:
        run_task = task
        continues = 0
        while True:
            text = run_delegated(
                bot,
                run_task,
                project=project,
                registry=registry,
                extra_context=extra_context,
                max_rounds=rounds,
                stall_rounds=stall,
                collect_tool_events=tool_events,
                provider=_planning_provider(phase, project.id),
            )
            if not hit_round_cap(text) or continues >= _MAX_AUTO_CONTINUES:
                break
            # Out of rounds while still getting somewhere: carry on once, from where it stopped,
            # instead of asking the person to type "continue".
            progressed = _changed_something(tool_events) if role in _PRODUCER_ROLES else bool(tool_events)
            if not progressed:
                break
            continues += 1
            run_task = _continue_task(task, tool_events)
    except BaseException:
        # Never leave a ledger row "in progress" for a run that blew up.
        if task_id:
            try:
                db.update_task_status(task_id, "failed")
            except Exception:
                pass
        raise
    stalled = hit_stall(text)
    round_capped = hit_round_cap(text) or stalled
    # run_bot turns an exception into "<Name> failed: ..." -- a planning cat's reply would otherwise
    # count as done just because planning roles need no tools (a 429 showed up as "Done").
    # The reply may carry a "[Name / role]" header first, so look at every line start.
    failed = bool(re.search(rf"^{re.escape(bot.name)} failed:", text, re.MULTILINE))
    if not failed:
        from backend.harness import model_block

        failed = model_block.pending(project.id) is not None
    empty = _EMPTY_REPLY in text
    check_failed = False
    if not failed and _changed_something(tool_events):
        text, check_failed = _check_and_fix(bot, task, text, tool_events, project=project, registry=registry, extra_context=extra_context, phase=phase)
    text = _format_delegation_result(bot, text)
    if phase in _PLANNING_PHASES or phase == "blueprint":
        worked = True  # a plan is text: it needs no tool call to count
    elif role in _PRODUCER_ROLES:
        worked = _changed_something(tool_events)
    else:
        worked = bool(tool_events) or role not in _BUILDER_ROLES
    verified = not failed and not empty and worked and not check_failed
    if not verified and not failed:
        text += (
            "\n\n⚠ Nothing was written or run on disk this phase"
            + (" (the model answered with nothing)" if empty else "")
            + f". {bot.name}'s reply above is talk, not a verified handoff; treat this phase as "
            "still open."
        )
    if stalled:
        text += (
            f"\n\n⛔ {bot.name} stopped making progress ({_STALL_ROUNDS} tool rounds in a row "
            "changed nothing), so the harness ended this work order instead of letting it loop. "
            "Marked blocked."
        )
    elif round_capped:
        text = text.replace(_SEND_CONTINUE, "").rstrip()
        runs = f", {continues + 1} runs" if continues else ""
        text += (
            f"\n\n⛔ {bot.name} reached the most one work order may take ({rounds or 'default'} "
            f"tool rounds{runs}) while still working, so it was stopped and marked blocked. What "
            "it finished is on disk; the next run of the crew picks up the rest."
        )
    return SopTurn(
        bot=bot,
        text=text,
        tool_events=tool_events,
        phase=phase,
        verified=verified,
        task_id=task_id,
        round_capped=round_capped,
        failed=failed,
    )


def _init_project_state(project: Project, registry: Registry) -> dict:
    """Seed/return the shared ``ProjectState`` this run tracks on
    ``project.state`` (CrewAI-style shared context): a persisted, disk-backed
    record of what phase is running, what each phase produced, and every
    file/command a bot's tools actually touched — independent of the SOP
    engine's own local ``prior``/``turns`` variables, so a UI (or another
    process) can inspect real progress without replaying the whole turn.
    """
    state = project.state
    state.setdefault("created_artifacts", [])
    state.setdefault("phase_outputs", {})
    state["active_phase"] = ""
    tasks: list[dict] = []
    for phase, role in PHASE_ROLES:
        bot = _bot_for_role(registry, project, role)
        tasks.append(
            {
                "phase": phase,
                "role": role,
                "bot_id": bot.id if bot else "",
                "bot_name": bot.name if bot else "",
                "status": "pending" if bot else "skipped",
            }
        )
    state["tasks"] = tasks
    return state


def _task_row(state: dict, phase: str) -> dict | None:
    for row in state.get("tasks") or []:
        if isinstance(row, dict) and row.get("phase") == phase and row.get("status") != "done":
            return row
    return None


def _mark_phase_active(project: Project, phase: str, bot: Bot) -> None:
    state = project.state
    state["active_phase"] = phase
    row = _task_row(state, phase)
    if row is None:
        row = {"phase": phase, "role": (bot.role or ""), "status": "pending"}
        state.setdefault("tasks", []).append(row)
    row["bot_id"] = bot.id
    row["bot_name"] = bot.name
    row["status"] = "in_progress"


def _mark_phase_done(project: Project, turn: "SopTurn") -> None:
    state = project.state
    row = _task_row(state, turn.phase)
    if row is not None:
        row["status"] = turn.ledger_status
    outputs = state.setdefault("phase_outputs", {})
    outputs[turn.phase or turn.bot.id] = {
        "bot_id": turn.bot.id,
        "bot_name": turn.bot.name,
        "verified": turn.verified,
        "tool_events": list(turn.tool_events),
        "text": turn.text[:4000],
    }
    if turn.tool_events:
        artifacts = state.setdefault("created_artifacts", [])
        for line in turn.tool_events:
            if line not in artifacts:
                artifacts.append(line)
        if len(artifacts) > 300:
            state["created_artifacts"] = artifacts[-300:]


def run_project_room_turn(
    message: str,
    *,
    project: Project,
    registry: Registry,
    on_turn: Callable[[SopTurn], None] | None = None,
    on_phase_start: Callable[[str, Bot], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    take_steer: Callable[[], str | None] | None = None,
    roles: tuple[str, ...] | None = None,
) -> list[SopTurn]:
    """Run one Cat Crew work message to completion.

    ``roles`` (from ``platform/orchestrator.classify_intent``) limits the run to
    those specialists, in pipeline order; ``None`` runs the whole pipeline.

    Returns every ``SopTurn`` produced (also delivered live via ``on_turn``
    as each finishes, so a UI can stream them instead of waiting for the
    whole pipeline). An ``@mention`` routes straight to that bot and does
    not touch the phase state; anything else is treated as a fresh brief
    and runs the full Architect -> Lead -> DevOps -> Developer -> Designer
    -> QA pipeline, saving the registry once at the end (matching the old
    ``_delegate_team`` sequential loop this replaces).

    ``on_phase_start``, when given, fires right before each bot's turn
    begins (mention, each pipeline phase, the QA-fix retry, and any
    spontaneous inclusion) — a UI can use it to show real progress
    ("Heimerdinger is working…") instead of a single static placeholder
    for the whole turn, which is all ``on_turn`` alone can offer since it
    only fires once a bot's turn has *finished*.

    ``should_stop`` is checked between phases (a phase bot's own tool loop
    already respects the ambient abort flag via ``current_abort()`` inside
    ``run_delegated``/``run_bot`` — this catches the gap between one
    phase finishing and the next one starting).

    ``take_steer``, when given, is drained right before each phase (and
    the mention/qa-fix/spontaneous turns) is dispatched, and any queued
    text is folded into that turn's ``extra_context`` as a flagged,
    highest-priority note. There is no single continuous LLM loop here to
    interrupt mid-round the way ChatWorker's agent loop does — each phase
    is a whole separate ``run_delegated`` call — so steering text lands on
    the NEXT phase to start, not the one already running.
    """
    turns: list[SopTurn] = []

    def _steer_note(context: str) -> str:
        if context and _HANDOFF_NOTE not in context:
            context = context + "\n\n" + _HANDOFF_NOTE
        if take_steer is None:
            return context
        text = take_steer()
        if not text:
            return context
        note = (
            "URGENT — the user just steered this project mid-pipeline; treat this "
            "as the highest-priority instruction for your task below:\n" + text
        )
        return (context + "\n\n" + note).strip() if context else note

    def _emit(turn: SopTurn) -> None:
        turns.append(turn)
        project.remember(turn.text, role="bot", bot_id=turn.bot.id)
        _mark_phase_done(project, turn)
        if turn.task_id:
            try:
                db.add_task_result(turn.task_id, turn.ledger_status, turn.text[:4000])
            except Exception:
                pass
        if on_turn is not None:
            on_turn(turn)

    def _start(phase: str, bot: Bot, *, title: str = "", description: str = "") -> str:
        _mark_phase_active(project, phase, bot)
        # Ledger first, UI callback second: create_task staffs the bot on the
        # project and announces it on the bus, so the sidebar row pops in
        # *before* on_phase_start flips its status pill to "working" — the
        # order the user sees is "Pulse joins the crew" then "Pulse is coding".
        task_id = ""
        try:
            task_id = db.create_task(
                project.id,
                bot.id,
                title or f"{bot.name}: {phase}",
                description,
                created_by="lead",
            )
            # The task is being worked on right now — the stepper must not
            # read "Pending" while the bot is mid-run.
            db.update_task_status(task_id, "in_progress")
        except Exception:
            pass
        if on_phase_start is not None:
            on_phase_start(phase, bot)
        return task_id

    mentioned = parse_mention(message, registry)
    if mentioned is not None:
        task_id = _start("mention", mentioned, title="Direct @mention", description=message[:2000])
        mention_task = message
        if task_id:
            mention_task += f"\n\nYour task_id for report_task_results is: {task_id}"
        _emit(
            _run_one(
                mentioned,
                mention_task,
                project=project,
                registry=registry,
                extra_context=_steer_note(""),
                phase="mention",
                task_id=task_id,
            )
        )
        save_registry(registry)
        return turns

    brief = message
    if not str(project.state.get("kickoff") or "").strip():
        project.state["kickoff"] = brief[:8000]

    # Intent router picked specific specialists: run just those (pipeline order),
    # each with the plain "do what was asked" task instead of the phase scaffolding.
    order: list[tuple[str, Bot]] = []
    if roles:
        seen: set[str] = set()
        for phase, role in PHASE_ROLES:
            if role in roles and role not in seen:
                seen.add(role)
                bot = _bot_for_role(registry, project, role)
                if bot is not None:
                    order.append((phase, bot))
        for role in roles:  # legal / accountant / marketing have no fixed phase
            if role not in seen:
                seen.add(role)
                bot = _bot_for_role(registry, project, role)
                if bot is not None:
                    order.append(("spontaneous", bot))
    if order:
        project.state.setdefault("created_artifacts", [])
        project.state.setdefault("phase_outputs", {})
        try:
            db.ensure_project(project.id, project.name, (project.folders or [""])[0])
        except Exception:
            pass
        prior = ""
        for phase, bot in order:
            if should_stop is not None and should_stop():
                break
            task_id = _start(phase, bot, description=brief[:2000])
            turn = _run_one(
                bot,
                _direct_task(brief, bot, task_id=task_id),
                project=project,
                registry=registry,
                extra_context=_steer_note(prior),
                phase=phase,
                task_id=task_id,
            )
            _emit(turn)
            prior = _append_prior(prior, turn.text)
            project.state["sop_phase"] = phase
        else:
            project.state["sop_phase"] = "done"
        save_registry(registry)
        return turns

    if _wants_resume(project, message):
        pipe = project.state["pipeline"]
        note = message.strip() if message.strip() != str(pipe["brief"]).strip() else ""
        brief = str(pipe["brief"])
    else:
        pipe = {"brief": brief, "done": [], "work_orders": [], "orders_done": [], "checks": []}
        note = ""
    project.state["pipeline"] = pipe
    done_phases = set(pipe.get("done") or [])

    _init_project_state(project, registry)
    try:
        db.ensure_project(project.id, project.name, (project.folders or [""])[0])
    except Exception:
        pass
    prior = ""
    outputs = project.state.get("phase_outputs") or {}
    for phase, _role in PHASE_ROLES:
        if phase in done_phases:
            # Resumed: the finished phases are not run again, their output is handed on as before.
            prior = _append_prior(prior, str((outputs.get(phase) or {}).get("text") or ""))
            row = _task_row(project.state, phase)
            if row is not None:
                row["status"] = "done"
    if note:
        prior = _append_prior(prior, f"The user's latest message: {note}")

    def _stopping() -> bool:
        return should_stop is not None and should_stop()

    def _phase_turn(phase: str, bot: Bot, context: str, *, title: str = "", body: str | None = None, budget: int | None = None) -> SopTurn:
        task_body = body if body is not None else _phase_task_body(phase, brief)
        task_id = _start(phase, bot, title=title, description=brief[:2000])
        turn = _run_one(
            bot,
            _with_task_id(task_body, task_id),
            project=project,
            registry=registry,
            extra_context=_steer_note(context),
            phase=phase,
            task_id=task_id,
            budget=budget,
        )
        if phase in _PRODUCER_PHASES and not turn.verified and not turn.failed and not turn.round_capped and not _stopping():
            # The cat looked around and stopped (or said nothing). Its reply goes on record, and it
            # gets one more go with that fact spelled out, before anyone builds on top of nothing.
            _emit(turn)
            redo_id = _start(phase, bot, title=f"{title or f'{bot.name}: {phase}'} (second attempt)", description=brief[:2000])
            turn = _run_one(
                bot,
                _redo_note(phase) + _with_task_id(task_body, redo_id),
                project=project,
                registry=registry,
                extra_context=_steer_note(_append_prior(context, turn.text)),
                phase=phase,
                task_id=redo_id,
                budget=budget,
            )
        _emit(turn)
        project.state["sop_phase"] = phase
        return turn

    def _build(bot: Bot, context: str) -> tuple[bool, bool, str]:
        """(any slice built, every slice built, context). One work order at a time when Vanguard
        split the build; otherwise one build turn."""
        orders = [str(o) for o in pipe.get("work_orders") or []]
        if not orders:
            turn = _phase_turn("build", bot, context)
            return turn.verified, turn.verified, _append_prior(context, turn.text)
        finished = pipe.setdefault("orders_done", [])
        for index, order in enumerate(orders, 1):
            if _stopping():
                break
            if order in finished:
                continue
            turn = _phase_turn(
                "build",
                bot,
                context,
                title=f"{bot.name}: {index}/{len(orders)} {order[:60]}",
                body=_work_order_task(brief, orders, index),
                budget=PHASE_BUDGETS["build-item"],
            )
            context = _append_prior(context, turn.text)
            if turn.verified:
                finished.append(order)
            elif turn.failed:
                break
        return bool(finished), len(finished) == len(orders), context

    def _qa(bot: Bot, context: str) -> tuple[bool, str]:
        """QA, and on FAIL the developer fixes and QA checks again: up to ``_QA_CYCLES`` checks."""
        for cycle in range(1, _QA_CYCLES + 1):
            if _stopping():
                break
            smoke = _smoke_report(project, pipe.get("checks") or [])
            health = _project_check(project)
            body = _phase_task_body("qa", brief) + (f"\n\n{smoke}" if smoke else "")
            if health is not None:
                body += f"\n\n{health.report()}\n(A FAIL in the harness project check is a FAIL of this QA pass.)"
            title = f"{bot.name}: qa" + (f" (check {cycle} of {_QA_CYCLES})" if cycle > 1 else "")
            turn = _phase_turn("qa", bot, context, title=title, body=body)
            context = _append_prior(context, turn.text)
            if turn.failed:
                return False, context
            if not _qa_verdict_failed(turn.text):
                return True, context
            if cycle == _QA_CYCLES or _stopping():
                break
            fixer = _bot_for_role(registry, project, "developer") or _bot_for_role(registry, project, "devops")
            if fixer is None:
                break
            fix_body = (
                f"QA checked the work and FAILED it (check {cycle} of {_QA_CYCLES}). Fix every "
                "failing item below on disk -- failing automated tests first. Do not weaken or "
                "delete a test to make it pass unless the test itself is wrong, and say so if it "
                "is. Re-run the tests and the build, then list what you changed.\n\n"
                f"Original brief:\n{brief}\n\nQA's report:\n{_qa_verdict_body(turn.text)[:6000]}"
            )
            fix_turn = _phase_turn("qa-fix", fixer, context, title=f"Fix QA failures (round {cycle})", body=fix_body)
            context = _append_prior(context, fix_turn.text)
            if fix_turn.failed:
                break
        return False, context

    finished_all = True
    for phase, role in PHASE_ROLES:
        if _stopping():
            finished_all = False
            break
        if phase in done_phases:
            continue
        bot = _bot_for_role(registry, project, role)
        if bot is None:
            continue
        if phase == "build":
            any_built, all_built, prior = _build(bot, prior)
            ok, produced = all_built, any_built
        elif phase == "qa":
            ok, prior = _qa(bot, prior)
            produced = ok
        else:
            turn = _phase_turn(phase, bot, prior)
            prior = _append_prior(prior, turn.text)
            ok = produced = turn.verified
            if phase == "distribution" and not turn.failed:
                pipe["work_orders"] = _work_orders(turn.text)
                pipe["orders_done"] = []
                pipe["checks"] = _acceptance_checks(turn.text)
        if ok and phase not in pipe["done"]:
            pipe["done"].append(phase)
        if phase in ("devops", "build") and not produced:
            # Design and QA on top of an empty build only burn time and tokens: stop and say so.
            if turns:
                turns[-1].text += (
                    f"\n\n⛔ The crew stopped after {phase}: nothing was built, so design and QA "
                    "would have nothing to work on. Send continue to try again, or pick a "
                    "stronger Coding model on the Models page."
                )
            finished_all = False
            break
        if phase == "distribution":
            for extra_bot in _spontaneous_bots(registry, project, brief):
                extra_turn = _phase_turn("spontaneous", extra_bot, prior, body=brief)
                prior = _append_prior(prior, extra_turn.text)
    if finished_all:
        project.state["sop_phase"] = "done"
    save_registry(registry)
    return turns


def _with_task_id(text: str, task_id: str) -> str:
    return f"{text}\n\nYour task_id for report_task_results is: {task_id}" if task_id else text
