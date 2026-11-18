"""M9 Delivery tracking: the PM records execution progress on approved tasks.

The agents never write progress — only the PM does, one PATCH at a time, audited.
Rules: todo -> 0%, done -> 100%, in_progress keeps or sets the given percent.
The portfolio overview reads these fields, so the command center stays in sync.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import Task, User

ALLOWED_STATUSES = ("todo", "in_progress", "done")


class DeliveryError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code, self.message, self.status_code = code, message, status_code
        super().__init__(message)


def update_task_progress(db: Session, *, project_id: int, task_id: int,
                         status: str, progress_pct: int | None, pm: User) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.project_id == project_id))
    if task is None:
        raise DeliveryError("task_not_found", "Task not found", 404)
    if task.review_status != "approved":
        raise DeliveryError("task_not_approved", "Delivery tracking is for approved tasks only", 409)

    before = {"status": task.status, "progress_pct": task.progress_pct}
    if status == "done":
        progress = 100
    elif status == "todo":
        progress = 0
    else:  # in_progress: keep the current value unless the PM sent one
        progress = progress_pct if progress_pct is not None else task.progress_pct
    task.status = status
    task.progress_pct = int(progress)

    write_audit(
        db,
        actor=pm.email,
        action="task.progress",
        entity_type="task",
        entity_id=task.id,
        before=before,
        after={"status": task.status, "progress_pct": task.progress_pct},
    )
    return task
