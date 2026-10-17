"""Problem report -> team lead -> fix plan -> fixes -> QA (see docs/PI_EVALUATION_AND_AGENT_PLAN.md).

The user reports a problem in a cat's own chat, or in Cat Crew. Either way it becomes one ``bug``
task in the ledger and the same steps run, so the two chats differ only in what they show:

1. The lead (Vanguard) gets the report plus facts the harness collected (the reporting cat's last
   tasks and files, the project's recent changes) in a *fresh* context, and writes the fix plan by
   calling ``create_fix_task`` once per step. Each call is a ``fix`` work order in the ledger, so
   the plan is data the harness can run, not prose it has to parse.
2. Each fix runs with only its own work order: the step, the acceptance criteria and a short
   excerpt of the plan. No conversation history.
3. QA verifies the acceptance criteria. A FAIL becomes one more fix for the same cat, with QA's
   verdict, and QA runs again, up to ``MAX_FIX_ATTEMPTS`` times. The bug closes only on PASS.
"""

from __future__ import annotations

from typing import Callable

from backend.db import ledger as db
from backend.platform.models import Bot, Project, Registry
from backend.platform.registry import save_registry
from backend.platform.sop import SopTurn, _append_prior, _bot_for_role, _qa_verdict_failed, _run_one

MAX_FIX_ATTEMPTS = 2
_FIXER_ROLES = frozenset({"developer", "designer", "devops", "prompt_engineer", "marketing", "legal", "pr"})


def _facts(project: Project, reporter: Bot | None) -> str:
    """What the ledger knows, so the lead starts from facts instead of asking the user."""
    lines: list[str] = []
    try:
        tasks = db.list_tasks_with_summary(project.id, 60)
        if reporter is not None:
            mine = [t for t in tasks if t.get("assigned_to") == reporter.id and t.get("kind") in (None, "build", "fix")][:3]
            for t in mine:
                files = [c["file_path"] for c in db.file_changes_for_task(t["id"], 8)]
                said = " ".join(str(t.get("latest_summary") or "").split())[:200]
                lines.append(
                    f"- {reporter.name}'s task [{t.get('status')}] {t.get('title')}"
                    + (f" — files: {', '.join(files)}" if files else "")
                    + (f" — said: {said}" if said else "")
                )
        recent: list[str] = []
        for change in db.list_file_changes(project.id, 12):
            path = str(change.get("file_path") or "")
            if path and path not in recent:
                recent.append(path)
        if recent:
            lines.append("- recently changed files: " + ", ".join(recent[:10]))
    except Exception:  # noqa: BLE001 - facts are a help; the plan can still be written without them
        pass
    folder = (project.folders or [""])[0]
    if folder:
        lines.append(f"- project folder: {folder}")
    return "\n".join(lines) or "- (nothing recorded yet)"


def _plan_prompt(bug_id: str, plan_id: str, report: str, who: str, facts: str) -> str:
    return (
        f"A problem was reported{who}. The bug task_id to use with create_fix_task is: {bug_id}\n\n"
        f"Report:\n{report[:2000]}\n\n"
        f"Facts from the project ledger (not guesses):\n{facts}\n\n"
        "Write the fix plan. Read only what you need: ARCHITECTURE.md and the files named above or "
        "in the report (read_file, search_files). Do not change any file yourself.\n"
        "Then call create_fix_task once per step (at most 6), in the order they must be done:\n"
        "- assignee: the cat that owns that kind of work (Pixel: styling, layout, UX; "
        "Pulse: application code; Forge: install, build, environment).\n"
        "- instructions: exactly what to change and where (file paths, wrong behaviour, expected "
        "behaviour). The cat will not see the rest of the plan.\n"
        "- acceptance: how it is checked (a command that must exit 0, or what must be visible in "
        "the page). QA verifies these after the fixes.\n"
        "If you cannot tell what is wrong from the report and the files, make the first step a "
        "diagnosis for the most likely cat, with the acceptance 'the cause is found and written down'.\n"
        f"Finish with report_task_results (task_id: {plan_id}) and a three-line summary of the plan."
    )


def _fix_prompt(fix: dict, report: str, who: str) -> str:
    return (
        f"WORK ORDER (fix). Your task_id for report_task_results is: {fix['id']}\n"
        f"Problem reported{who}: {report[:1500]}\n\n"
        f"Your step: {fix.get('title')}\n"
        f"What to do: {fix.get('description')}\n"
        f"Accepted when: {fix.get('acceptance')}\n\n"
        "Do exactly this step on the real files, then run the build or check yourself "
        "(run_command) before you report. Call report_task_results with status done only if the "
        "acceptance holds; otherwise status blocked, and say what stopped you."
    )


def _review_prompt(review_id: str, report: str, criteria: list[str]) -> str:
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1)) or "1. The reported problem no longer occurs."
    return (
        f"Verify the fix for this problem: {report[:1200]}\n\n"
        f"Check every criterion below with real evidence (run the commands; for anything visible, "
        f"serve the page and use browser_check):\n{numbered}\n\n"
        "Start your reply with PASS if all hold, or FAIL followed by which ones do not. "
        f"Your task_id for report_task_results is: {review_id}"
    )


def run_bug_turn(
    report: str,
    *,
    project: Project,
    registry: Registry,
    reporter_id: str = "",
    on_turn: Callable[[SopTurn], None] | None = None,
    on_phase_start: Callable[[str, Bot], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    take_steer: Callable[[], str | None] | None = None,
) -> list[SopTurn]:
    """Run one problem report to a verified fix (or an honest failure). Returns every turn."""
    text = (report or "").strip()
    lead = _bot_for_role(registry, project, "vanguard")
    if lead is None:
        raise LookupError("This project has no team lead (Vanguard) to plan the fix.")
    reporter = registry.bots.get(reporter_id) if reporter_id else None
    who_id = reporter.id if reporter is not None else "user"
    who = f" by the user in {reporter.name}'s chat" if reporter is not None else " by the user"
    turns: list[SopTurn] = []

    def stopped() -> bool:
        return bool(should_stop and should_stop())

    def emit(turn: SopTurn) -> None:
        turns.append(turn)
        if on_turn is not None:
            on_turn(turn)

    def steer(context: str) -> str:
        note = take_steer() if take_steer is not None else None
        if not note:
            return context
        urgent = "URGENT — the user just steered this work; treat it as the highest priority:\n" + note
        return (context + "\n\n" + urgent).strip()

    title = " ".join(text.split())[:80] or "Reported problem"
    bug_id = db.create_task(
        project.id, lead.id, f"Bug: {title}", text[:4000], created_by=who_id, kind="bug", reported_by=who_id
    )
    db.update_task_status(bug_id, "in_progress")

    # 1. The lead writes the plan, with fresh context, through create_fix_task.
    plan_id = db.create_task(
        project.id, lead.id, f"Fix plan: {title}", "", created_by=who_id, kind="plan",
        parent_id=bug_id, reported_by=who_id,
    )
    db.update_task_status(plan_id, "in_progress")
    if on_phase_start is not None:
        on_phase_start("fixplan", lead)
    plan_turn = _run_one(
        lead, _plan_prompt(bug_id, plan_id, text, who, _facts(project, reporter)),
        project=project, registry=registry, extra_context=steer(""), phase="fixplan", task_id=plan_id,
    )
    if db.get_task(plan_id)["status"] == "in_progress":
        db.add_task_result(plan_id, "done", plan_turn.text[:4000])
    emit(plan_turn)

    fixes = db.list_child_tasks(bug_id, "fix")
    if not fixes:
        # The lead wrote prose instead of tasks. One fix for the most likely cat keeps the flow
        # moving; the lead's notes ride along as the instructions.
        fixer = reporter if reporter is not None and (reporter.role or "").lower() in _FIXER_ROLES else None
        fixer = fixer or _bot_for_role(registry, project, "developer") or lead
        fid = db.create_task(
            project.id, fixer.id, f"Fix: {title}",
            f"{text[:1500]}\n\nThe lead's notes:\n{plan_turn.text[:1500]}", created_by=lead.id,
            kind="fix", parent_id=bug_id, acceptance="The reported problem no longer occurs.", reported_by=who_id,
        )
        fixes = [db.get_task(fid)]

    plan_note = _append_prior("", plan_turn.text)

    def run_fix(fix: dict) -> str:
        bot = registry.bots.get(str(fix.get("assigned_to") or "")) or _bot_for_role(registry, project, "developer") or lead
        db.update_task_status(fix["id"], "in_progress")
        if on_phase_start is not None:
            on_phase_start("fix", bot)
        turn = _run_one(
            bot, _fix_prompt(fix, text, who), project=project, registry=registry,
            extra_context=steer("Plan excerpt:\n" + plan_note), phase="fix", task_id=fix["id"],
        )
        if db.get_task(fix["id"])["status"] == "in_progress":
            db.add_task_result(fix["id"], turn.ledger_status, turn.text[:4000])
        emit(turn)
        return str(db.get_task(fix["id"])["status"])

    # 2. Each fix runs as its own work order.
    outcome: dict[str, str] = {}
    for fix in fixes:
        if stopped():
            break
        deps = [d for d in str(fix.get("depends_on") or "").split(",") if d]
        if any(outcome.get(d) != "done" for d in deps):
            db.add_task_result(fix["id"], "blocked", "A step this one depends on did not finish.")
            outcome[fix["id"]] = "blocked"
            continue
        outcome[fix["id"]] = run_fix(fix)

    # 3. QA verifies the acceptance criteria; a FAIL is one more fix for the same cat.
    criteria = [str(f.get("acceptance") or "").strip() for f in fixes if str(f.get("acceptance") or "").strip()]
    qa = _bot_for_role(registry, project, "qa")
    verdict = ""
    passed: bool | None = None
    attempt = 0
    last_fix = fixes[-1]
    while qa is not None and not stopped():
        review_id = db.create_task(
            project.id, qa.id, f"Verify: {title}", "\n".join(criteria), created_by=who_id, kind="review",
            parent_id=bug_id, acceptance="\n".join(criteria), attempt=attempt, reported_by=who_id,
        )
        db.update_task_status(review_id, "in_progress")
        if on_phase_start is not None:
            on_phase_start("qa", qa)
        review = _run_one(
            qa, _review_prompt(review_id, text, criteria), project=project, registry=registry,
            extra_context=steer(""), phase="qa", task_id=review_id,
        )
        verdict = review.text
        failed = _qa_verdict_failed(review.text)
        if db.get_task(review_id)["status"] == "in_progress":
            db.add_task_result(review_id, "failed" if failed else "done", review.text[:4000])
        emit(review)
        passed = not failed
        if passed or attempt >= MAX_FIX_ATTEMPTS:
            break
        attempt += 1
        again_id = db.create_task(
            project.id, str(last_fix["assigned_to"]), f"Fix again ({attempt}): {title}",
            f"{text[:1200]}\n\nQA rejected the previous fix:\n{verdict[:2500]}", created_by=qa.id,
            kind="fix", parent_id=bug_id, acceptance="\n".join(criteria), attempt=attempt, reported_by=who_id,
        )
        again = db.get_task(again_id)
        run_fix(again)
        last_fix = again

    # 4. Close the bug honestly: PASS only when QA said so.
    final = "blocked" if stopped() else ("done" if passed else ("failed" if passed is False else "unverified"))
    db.add_task_result(bug_id, final, f"{len(db.list_child_tasks(bug_id, 'fix'))} fix step(s). QA: {verdict[:1500] or 'not run'}")
    steps = "\n".join(f"· {registry.bots[f['assigned_to']].name if f['assigned_to'] in registry.bots else f['assigned_to']}: {f['title']} — {db.get_task(f['id'])['status']}" for f in db.list_child_tasks(bug_id, "fix"))
    headline = {
        "done": "Fixed and verified by QA.",
        "failed": f"Not fixed: QA still fails after {attempt} more attempt(s). The last verdict is above.",
        "unverified": "The fix steps ran but nothing verified them (there is no QA cat on this project).",
        "blocked": "Stopped before the problem was closed.",
    }[final]
    emit(SopTurn(bot=lead, text=f"Problem report closed: {headline}\n{steps}", phase="fixreport", verified=True, task_id=bug_id))
    save_registry(registry)
    return turns
