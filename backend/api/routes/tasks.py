from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api import deps, views
from backend.db import ledger

router = APIRouter(prefix="/api", tags=["tasks"])

STATUSES = {"pending", "in_progress", "blocked", "failed", "unverified", "done"}


class TaskCreate(BaseModel):
    assigned_to: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    description: str = ""


class TaskPatch(BaseModel):
    status: str


def _task_in_project(project_id: str, task_id: str) -> dict:
    deps.need_project(project_id)
    row = ledger.get_task_with_summary(task_id)
    if row is None or row["project_id"] != project_id:
        raise HTTPException(404, f"Unknown task {task_id!r}.")
    return row


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: str) -> list[dict]:
    deps.need_project(project_id)
    rows = ledger.list_tasks_with_summary(project_id, limit=500)
    return [views.task_view(r) for r in reversed(rows)]  # oldest first


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, body: TaskCreate) -> dict:
    project = deps.need_project(project_id)
    bot = deps.need_bot(body.assigned_to)
    deps.ledger_project(project)
    task_id = ledger.create_task(
        project.id, bot.id, deps.clean(body.title), deps.clean(body.description), created_by="user"
    )  # also staffs the agent (announces bot.staffed) and task.changed
    try:
        from backend.tools import refresh_project_structure_doc

        refresh_project_structure_doc(project.id)
    except Exception:  # noqa: BLE001 - the doc refresh is a nicety
        pass
    return views.task_view(ledger.get_task_with_summary(task_id) or {})


@router.get("/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str) -> dict:
    row = _task_in_project(project_id, task_id)
    out = views.task_view(row)
    out["results"] = [views.result_view(r) for r in ledger.list_results_for_task(task_id)]
    return out


@router.patch("/projects/{project_id}/tasks/{task_id}")
def patch_task(project_id: str, task_id: str, body: TaskPatch) -> dict:
    _task_in_project(project_id, task_id)
    status = deps.clean(body.status).lower()
    if status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}.")
    ledger.update_task_status(task_id, status)
    return views.task_view(ledger.get_task_with_summary(task_id) or {})


@router.get("/projects/{project_id}/changes")
def list_changes(project_id: str, limit: int = Query(default=8, ge=1, le=100)) -> list[dict]:
    deps.need_project(project_id)
    return [views.change_view(r) for r in ledger.list_file_changes(project_id, limit)]


@router.get("/diffs/{diff_uuid}")
def get_diff(diff_uuid: str) -> dict:
    row = ledger.get_file_change(diff_uuid)
    if row is None or not deps.owns_project_id(str(row.get("project_id") or "")):
        raise HTTPException(404, f"Unknown diff {diff_uuid!r}.")
    return {
        "diff_uuid": row["diff_uuid"],
        "file_path": row["file_path"],
        "agent_id": row["agent_id"],
        "diff_content": row.get("diff_content") or "",
        "created_at": row["created_at"],
    }
