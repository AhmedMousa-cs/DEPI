"""Shared builders for the plan-version / Agent 4 tests (real DB, no LLM)."""
from datetime import date

from app.agents.agent3_schedule import run_agent3
from app.db.models import Requirement, Task, TaskDependency, TeamMember, UserStory
from app.db.session import SessionLocal
from app.schemas.scheduling import TaskInput, TeamMemberInput
from app.services.plan_versions import build_snapshot, load_plan_inputs

DEFAULT_TASKS = [  # (title, estimate_hours, assignee index or None, depends_on indexes)
    ("Design booking flow", 16, 0, []),
    ("Build booking API", 24, 0, [0]),
    ("Build booking UI", 24, 1, [0]),
    ("Test end to end", 8, 1, [1, 2]),
]
DEFAULT_MEMBERS = [("Ahmed", 40), ("Sara", 40)]


def seed_plan_data(project_id: int, tasks=DEFAULT_TASKS, members=DEFAULT_MEMBERS, review_status="approved"):
    """Approved requirement + story + approved tasks (with dependencies) + team members."""
    with SessionLocal() as s:
        req = Requirement(project_id=project_id, type="functional", text="Online booking", status="approved",
                          quote_verified=True, confidence=0.9, created_by_agent="agent1_extract")
        s.add(req)
        s.flush()
        story = UserStory(project_id=project_id, requirement_id=req.id, text="As a patient I book online",
                          acceptance_criteria=["works"], confidence=0.9, review_status="approved")
        s.add(story)
        s.flush()
        ms = [TeamMember(project_id=project_id, name=n, capacity_hours_per_week=h) for n, h in members]
        s.add_all(ms)
        s.flush()
        rows = []
        for title, hours, who, _deps in tasks:
            t = Task(project_id=project_id, story_id=story.id, title=title, estimate_hours=hours,
                     assignee_member_id=ms[who].id if who is not None else None,
                     review_status=review_status, locked_by_pm=True, created_by_agent="agent2_plan")
            s.add(t)
            rows.append(t)
        s.flush()
        for t, (_title, _h, _w, deps) in zip(rows, tasks):
            for d in deps:
                s.add(TaskDependency(task_id=t.id, depends_on_task_id=rows[d].id))
        s.commit()
        return {"requirement_id": req.id, "story_id": story.id,
                "task_ids": [t.id for t in rows], "member_ids": [m.id for m in ms]}


def build_draft_snapshot(project_id: int, *, start=date(2026, 11, 15), overrides=()):
    """What the agent3.schedule job does: DB inputs -> Agent 3 (no LLM) -> snapshot."""
    with SessionLocal() as s:
        inputs = load_plan_inputs(s, project_id)
    by_task = {o["task_id"]: o for o in overrides}
    tasks = []
    for t in inputs.tasks:
        o = by_task.get(t["id"], {})
        assignee = o["assignee_member_id"] if "assignee_member_id" in o else t["assignee_member_id"]
        tasks.append(TaskInput(
            id=t["id"], title=t["title"], estimate_hours=t["estimate_hours"],
            duration_days=o.get("duration_days"),
            assignee_id=assignee, depends_on=inputs.deps[t["id"]],
        ))
    members = [TeamMemberInput(id=m["id"], name=m["name"], capacity_hours_per_week=m["capacity_hours_per_week"],
                               available_from=m["available_from"], available_to=m["available_to"])
               for m in inputs.members]
    out = run_agent3(tasks, members, start)
    return inputs, build_snapshot(inputs=inputs, output=out, overrides=list(overrides))
