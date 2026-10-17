"""Vanguard (team lead) tools: turn a problem report into a fix plan the crew can execute."""

from __future__ import annotations

# A fix plan longer than this is a rewrite, not a fix: the lead must cut it down or say so.
MAX_FIX_TASKS = 6
# Roles that fix things. QA is not one: it verifies the result automatically after the fixes.
_FIXER_ROLES = ("developer", "designer", "devops", "prompt_engineer", "marketing", "legal", "pr")


def _fixers(registry, project):
    return [b for b in registry.project_bots(project) if (b.role or "").strip().lower() in _FIXER_ROLES]


def create_fix_task(
    bug_task_id: str,
    assignee: str,
    title: str,
    instructions: str,
    acceptance: str,
    depends_on: str = "",
) -> str:
    """Add one step to the fix plan for a reported problem. Call it once per step, in order.

    The step becomes a work order for the assigned cat: it gets exactly these instructions and
    acceptance criteria (not the whole conversation), does the work, and QA then checks the
    acceptance criteria. Use it only when you were given a bug task_id.

    Args:
        bug_task_id: The task_id of the bug you were given.
        assignee: The cat that owns this kind of work, by name or role (for example "Pixel" or
            "designer" for styling and layout, "Pulse" or "developer" for application code,
            "Forge" or "devops" for build, install and environment problems).
        title: A short name for the step (under 80 characters).
        instructions: Exactly what to change and where: file paths, the behaviour that is wrong
            and the behaviour that is expected. The cat will not see the rest of the plan.
        acceptance: How the step is checked: a command that must exit 0, or what must be
            visible or working in the page. Concrete and checkable.
        depends_on: Optional task_ids (comma-separated) of earlier steps that must be done first.

    Returns:
        The new fix task's id, or an ``Error:`` message that says how to correct the call.
    """
    from backend.db import ledger as db
    from backend.platform.registry import load_registry

    bug = db.get_task(str(bug_task_id or "").strip())
    if bug is None or (bug.get("kind") or "") != "bug":
        return "Error: unknown bug_task_id. Use the task_id written in your assignment (it starts with 'task_')."
    if not str(title or "").strip() or not str(instructions or "").strip() or not str(acceptance or "").strip():
        return "Error: title, instructions and acceptance are all required."
    existing = db.list_child_tasks(bug["id"], "fix")
    if len(existing) >= MAX_FIX_TASKS:
        return f"Error: a fix plan has at most {MAX_FIX_TASKS} steps. Merge steps or finish the plan now."

    registry = load_registry()
    project = registry.projects.get(bug["project_id"])
    if project is None:
        return "Error: the bug's project no longer exists."
    wanted = str(assignee or "").strip().lower()
    fixers = _fixers(registry, project)
    bot = next(
        (b for b in fixers if wanted in {b.id.lower(), (b.name or "").lower(), (b.role or "").lower()}),
        None,
    )
    if bot is None:
        valid = ", ".join(f"{b.name} ({b.role})" for b in fixers) or "none on this project"
        return f"Error: no cat named {assignee!r} can take a fix. Assign one of: {valid}."

    known = {t["id"] for t in existing}
    deps = [d.strip() for d in str(depends_on or "").split(",") if d.strip()]
    unknown = [d for d in deps if d not in known]
    if unknown:
        return f"Error: depends_on has unknown fix task ids {unknown}. Use ids returned by earlier create_fix_task calls."

    task_id = db.create_task(
        bug["project_id"],
        bot.id,
        str(title).strip(),
        str(instructions).strip(),
        created_by=str(bug.get("assigned_to") or "lead"),
        kind="fix",
        parent_id=bug["id"],
        acceptance=str(acceptance).strip(),
        depends_on=",".join(deps),
        reported_by=str(bug.get("reported_by") or ""),
    )
    return f"Created fix task {task_id} for {bot.name} ({len(existing) + 1} of at most {MAX_FIX_TASKS})."
