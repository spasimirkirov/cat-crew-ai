"""Hermes-style auto skill builder (Phase 15 §4 / Task #26).

Hooked from ``tools.py``'s ``report_task_results`` dispatch: after a bot
closes out a task, one small, cheap, no-tools LLM call judges whether the
task's file changes contain a genuinely novel, reusable pattern worth
saving as a skill. This is a classifier + formatter, not another agent
turn — bounded output, no tool access, reusing whatever provider is
already bound to the calling bot's turn (``harness/runtime.py``'s
``current_provider()``) rather than spinning up a second one.

A proposed skill is written to the custom skills overlay
(``agent_context.custom_skills_dir()``) tagged ``type='auto_generated'``
and is deliberately NOT auto-assigned to any agent — ``maybe_propose_skill``
only ever adds a candidate to the catalog and emits a
``skill_proposal`` event (``harness/events.py``) for a human (or the
originating agent) to review; ``approve_proposal``/``dismiss_proposal``
below are the two outcomes of that review. Approving assigns the skill to
the agent that discovered it, matching the spec's own mockup ("Approve &
Assign"), not a global rollout.

Call site (``tools.py``): wrapped in ``maybe_propose_skill``'s own
try/except-everything shell, so this module never raises into and never
blocks ``report_task_results``'s return value for more than one bounded
model call.
"""

from __future__ import annotations

_PROPOSE_SYSTEM = (
    "You are reviewing one finished coding task for a reusable pattern.\n"
    "You will be shown the task's summary and the files it changed "
    "(diffs, possibly truncated).\n"
    "Decide: is there a genuinely novel, reusable technique here — "
    "something a *different* future task in this codebase would benefit "
    "from being told about (a workaround for a tricky library quirk, a "
    "non-obvious setup step, a pattern this codebase now follows) — as "
    "opposed to routine, one-off, or already-obvious work?\n"
    "Be conservative: most tasks are NOT worth saving. If in doubt, say NONE.\n\n"
    "If NOT worth saving, reply with exactly: NONE\n\n"
    "If worth saving, reply with a complete SKILL.md and nothing else — no "
    "commentary before or after, no code fences:\n"
    "---\n"
    "name: <short-kebab-case-slug>\n"
    "description: <one line, what a bot searching skills would need to see>\n"
    "version: 1.0.0\n"
    "target_roles: <comma-separated roles this helps, or leave blank for any>\n"
    "---\n\n"
    "<Markdown body explaining the pattern concretely enough to apply again>\n"
)

_MAX_DIFF_CHARS = 4000
_MAX_SUMMARY_CHARS = 800
_DONE_STATUSES = {"done", "pass", "passed", "complete", "completed"}


def _gather_context(task_id: str, summary: str) -> str:
    from backend.db import ledger as db

    parts = [f"Task summary:\n{(summary or '').strip()[:_MAX_SUMMARY_CHARS]}"]
    try:
        changes = db.file_changes_for_task(task_id)
    except Exception:  # noqa: BLE001 — best-effort context only
        changes = []
    if changes:
        parts.append(f"\n{len(changes)} file(s) changed:")
        budget = _MAX_DIFF_CHARS
        for change in changes:
            if budget <= 0:
                parts.append("\n... (more files changed, truncated)")
                break
            path = change.get("file_path") or "(unknown file)"
            diff = str(change.get("diff_content") or "")[: max(0, budget)]
            budget -= len(diff)
            parts.append(f"\n--- {path} ---\n{diff}")
    return "\n".join(parts)


def _parse_proposal(raw: str) -> str:
    text = (raw or "").strip()
    if not text or text.upper() == "NONE":
        return ""
    if text.startswith("```"):
        text = text.strip("`").strip()
        lines = text.splitlines()
        if lines and not lines[0].strip().startswith("---"):
            lines = lines[1:]
        text = "\n".join(lines).strip()
    if not text.startswith("---"):
        return ""
    return text


def maybe_propose_skill(
    task_id: str, summary: str, status: str, *, agent_id: str = ""
) -> None:
    """Best-effort, fire-and-forget: never raises, never blocks the caller
    for more than one small model call. Call this *after* the task's
    result/file-change rows are already committed — it reads them back."""
    try:
        _propose(task_id, summary, status, agent_id)
    except Exception:  # noqa: BLE001 — must never break report_task_results
        pass


def _propose(task_id: str, summary: str, status: str, agent_id: str) -> None:
    if (status or "").strip().lower() not in _DONE_STATUSES:
        return
    from ..harness.runtime import current_provider

    provider = current_provider()
    if provider is None:
        return
    context = _gather_context(task_id, summary)
    from backend.middleware.jev_gatekeeper import no_skill_in_task

    if no_skill_in_task(context):
        # Jev is sure this was a one-off: no generative call on the (often expensive) model.
        return
    messages = [
        {"role": "system", "content": _PROPOSE_SYSTEM},
        {"role": "user", "content": context},
    ]
    reply = provider.chat(messages, tools=None)
    proposal = _parse_proposal(reply.content or "")
    if not proposal:
        return
    _save_proposal(proposal, task_id, agent_id)


def _save_proposal(content: str, task_id: str, agent_id: str) -> str:
    """Write the proposed SKILL.md to the custom overlay, upsert it into
    the catalog as ``type='auto_generated'`` (unassigned), and emit the
    review event. Returns the new skill's db id, or ``""`` if the model's
    output didn't parse as a usable skill."""
    from ..agent_context import _parse_skill, custom_skills_dir, slugify_skill_name
    from backend.db import ledger as db
    from ..harness.events import emit_skill_proposal

    target_dir = custom_skills_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    fallback = f"auto-skill-{task_id[:8]}" if task_id else "auto-skill"
    provisional = target_dir / f"{fallback}.md"
    provisional.write_text(content.strip() + "\n", encoding="utf-8")
    skill = _parse_skill(provisional, fallback)
    if skill is None:
        provisional.unlink(missing_ok=True)
        return ""
    slug = slugify_skill_name(skill.name) or fallback
    final_path = target_dir / f"{slug}.md"
    if final_path != provisional:
        if final_path.exists():
            final_path.unlink()
        provisional.replace(final_path)
    skill_id = db.upsert_skill(
        slug=skill.name,
        name=skill.name,
        description=skill.description,
        playbook_path=str(final_path),
        type="auto_generated",
        version=skill.version,
        target_roles=",".join(skill.target_roles),
        source_url=f"task:{task_id}" if task_id else "",
    )
    emit_skill_proposal(
        skill_id=skill_id,
        name=skill.name,
        description=skill.description,
        agent_id=agent_id,
        task_id=task_id,
    )
    # Real-time: the Skills tab shows a "proposed" badge the moment the builder
    # finds a reusable pattern (project is looked up from the task, if any).
    try:
        from . import bus

        task = db.get_task(task_id) if task_id else None
        bus.publish(
            bus.SKILL_PROPOSED,
            {"skill_id": skill_id, "agent_id": agent_id, "task_id": task_id},
            project_id=str((task or {}).get("project_id") or ""),
        )
    except Exception:  # noqa: BLE001
        pass
    return skill_id


def approve_proposal(skill_id: str, agent_id: str) -> None:
    """The "Approve & Assign" action for a ``skill_proposal`` event —
    assigns the auto-generated skill to the agent that discovered it (not
    globally). Does not change ``type``; ``type='auto_generated'`` stays as
    provenance even once assigned, same as ``type='marketplace'`` does for
    a GitHub-imported skill."""
    from backend.db import ledger as db

    agent = (agent_id or "").strip()
    if not skill_id or not agent:
        return
    db.assign_skill(agent, skill_id)


def dismiss_proposal(skill_id: str) -> None:
    """The "Dismiss" action — deletes the unassigned candidate outright
    rather than leaving dead rows around; nothing else references it yet
    since it was never assigned."""
    from pathlib import Path

    from backend.db import ledger as db

    row = next((s for s in db.list_skills() if s.get("id") == skill_id), None)
    db.delete_skill(skill_id)
    if row:
        path = Path(str(row.get("playbook_path") or ""))
        try:
            from ..agent_context import is_bundled_skill

            if path.name and not is_bundled_skill(path):
                path.unlink(missing_ok=True)
        except OSError:
            pass
