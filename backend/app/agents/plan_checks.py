"""Deterministic professional-quality checks for an Agent 2 plan. No DB and no LLM.

Two severities:

- HARD: this is not a plan a team can execute (a requirement without a story, a story without a
  task, a dependency cycle, duplicates, an estimate out of range). Remaining HARD issues fail the
  agent run, so a broken plan can never reach the database or the PM.
- SOFT: professional polish (identical estimates, missing ordering, no critical path, missing
  rationale). They are fed back to the model and, if they survive, recorded in the audit trail for
  the PM — but they never block the plan.

The same rules live in evals/planning_metrics.py for scoring; this module is the gate the agent
itself runs before persisting anything.
"""
from __future__ import annotations

from app.schemas.planning import PlanningOutput, find_cycle
from app.services.quotes import normalize

MIN_ESTIMATE_HOURS = 0.5
MAX_ESTIMATE_HOURS = 80.0
MAX_TASKS_PER_STORY = 8
MAX_CRITICAL_SHARE = 3  # at most one third of the tasks may be "critical"


def _duplicates(items: list[tuple], label: str, issues: list[str]) -> None:
    seen: set[tuple] = set()
    for key in items:
        if key in seen:
            issues.append(f"duplicate {label} {key[-1][:60]!r}")
        seen.add(key)


def plan_issues(output: PlanningOutput, requirements: list[dict]) -> tuple[list[str], list[str]]:
    """Return (hard_issues, soft_issues). Empty hard list means the plan may be persisted.

    `requirements` is the approved-requirement shape the agent received: dicts with at least
    `id` and `priority`.
    """
    hard: list[str] = []
    soft: list[str] = []
    approved = {int(r["id"]) for r in requirements}
    priority = {int(r["id"]): (r.get("priority") or "medium").lower() for r in requirements}
    stories = output.stories
    tasks = output.tasks
    tasks_by_story: dict[str, list] = {}
    for task in tasks:
        tasks_by_story.setdefault(task.story_ref, []).append(task)

    # ---- coverage and linkage (hard) ----------------------------------------------------------------
    covered = {s.requirement_id for s in stories}
    missing = sorted(approved - covered)
    if missing:
        hard.append(f"approved requirements without any story: {missing}")
    extra = sorted(covered - approved)
    if extra:
        hard.append(f"stories reference requirements that are not approved: {extra}")
    orphans = sorted(s.ref for s in stories if not tasks_by_story.get(s.ref))
    if orphans:
        hard.append(f"stories without any task: {orphans}")

    # ---- duplicates (hard) --------------------------------------------------------------------------
    _duplicates([(s.requirement_id, normalize(s.text)) for s in stories], "story", hard)
    _duplicates([(t.story_ref, normalize(t.title)) for t in tasks], "task", hard)

    # ---- estimates (hard on range, soft on uniformity) ------------------------------------------------
    estimates = [float(t.estimate_hours) for t in tasks]
    out_of_range = sorted(
        t.ref for t in tasks if not MIN_ESTIMATE_HOURS <= float(t.estimate_hours) <= MAX_ESTIMATE_HOURS
    )
    if out_of_range:
        hard.append(f"task estimates outside {MIN_ESTIMATE_HOURS}..{MAX_ESTIMATE_HOURS} hours: {out_of_range}")
    if len(estimates) > 1 and len(set(estimates)) == 1:
        soft.append("every task has the same estimate; make them reflect the real work of each task")

    # ---- granularity (hard) --------------------------------------------------------------------------
    overloaded = sorted(ref for ref, group in tasks_by_story.items() if len(group) > MAX_TASKS_PER_STORY)
    if overloaded:
        hard.append(f"more than {MAX_TASKS_PER_STORY} tasks in stories: {overloaded}")

    # ---- cycles (hard, also enforced by the schema) ---------------------------------------------------
    cycle = find_cycle({t.ref: list(t.depends_on) for t in tasks})
    if cycle:
        hard.append(f"dependency cycle among tasks {cycle}")

    # ---- ordering (soft) -----------------------------------------------------------------------------
    if stories and any(s.order is None for s in stories):
        soft.append("stories are missing their order field")
    if tasks and any(t.order is None for t in tasks):
        soft.append("tasks are missing their order field")
    story_order = {s.ref: s.order for s in stories}
    order_by_ref = {t.ref: (story_order.get(t.story_ref), t.order) for t in tasks}
    if all(v is not None and v[0] is not None and v[1] is not None for v in order_by_ref.values()):
        for task in tasks:
            here = order_by_ref[task.ref]
            for dep in task.depends_on:
                there = order_by_ref.get(dep)
                if there is not None and here <= there:
                    soft.append(
                        f"task {task.ref} is not ordered after its dependency {dep} in the execution sequence"
                    )

    # ---- priority first (soft): high-priority work is scheduled before the rest -----------------------
    if stories and all(s.order is not None for s in stories):
        high_orders = [s.order for s in stories if priority.get(s.requirement_id) == "high"]
        other_orders = [s.order for s in stories if priority.get(s.requirement_id, "medium") != "high"]
        if high_orders and other_orders and max(high_orders) > min(other_orders):
            soft.append(
                "high-priority requirements are not all planned before the others: order stories by "
                "priority first (all high, then medium/low), dependencies second"
            )

    # ---- critical path (soft) ------------------------------------------------------------------------
    critical = [t.ref for t in tasks if t.is_critical]
    if tasks and not critical:
        soft.append("no task is marked is_critical: mark the tasks that block the first release")
    elif tasks and len(critical) > max(1, len(tasks) // MAX_CRITICAL_SHARE):
        soft.append(
            f"too many critical tasks ({len(critical)}/{len(tasks)}): mark only the blocking backbone"
        )

    # ---- rationale (soft) ----------------------------------------------------------------------------
    no_rationale = sorted(t.ref for t in tasks if not (t.rationale and t.rationale.strip()))
    if tasks and no_rationale:
        soft.append(f"tasks without a rationale: {no_rationale}")

    return hard, soft
