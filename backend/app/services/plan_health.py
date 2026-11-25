"""Deterministic review brief for Agent 4 (no LLM, no database writes).

The model is good at judgement and bad at arithmetic, so every number a reviewer needs is computed
here and returned as one compact payload: delivery signal, days late, critical-path concentration,
per-member workload, the longest dependency chain, and a ranked list of attention points.

Pure function of the plan snapshot: same snapshot in, same brief out (covered by tests).
"""
from __future__ import annotations

from datetime import date

MAX_ATTENTION = 10
DEFAULT_HOURS_PER_DAY = 8.0


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: float, places: int = 2) -> float:
    return round(value, places)


def longest_chain(tasks: list[dict], by_id: dict[int, dict]) -> list[int]:
    """Longest dependency chain by scheduled duration. Ties break on task id, so it is stable."""
    memo: dict[int, tuple[int, list[int]]] = {}

    def walk(tid: int, path: frozenset[int]) -> tuple[int, list[int]]:
        if tid in memo:
            return memo[tid]
        task = by_id.get(tid)
        if task is None or tid in path:  # cycle guard: bad data must not hang the review
            return (0, [])
        best_days, best_path = 0, []
        for dep in task.get("depends_on") or []:
            dep = int(dep)
            if dep not in by_id:
                continue
            days, chain = walk(dep, path | {tid})
            if days > best_days or (days == best_days and chain and (not best_path or chain < best_path)):
                best_days, best_path = days, chain
        total = best_days + max(0, int(_num(task.get("duration_days"))))
        memo[tid] = (total, best_path + [tid])
        return memo[tid]

    best = (0, [])
    for tid in sorted(by_id):
        candidate = walk(tid, frozenset())
        if candidate[0] > best[0]:
            best = candidate
    return best[1]


def plan_health(snapshot: dict) -> dict:
    """Facts a reviewer would otherwise add up by hand: schedule, workload, dependencies, risks."""
    tasks = list(snapshot.get("tasks") or [])
    sched = snapshot.get("schedule") or {}
    capacity = snapshot.get("capacity") or {}
    project = snapshot.get("project") or {}
    by_id = {int(t["id"]): t for t in tasks}

    start, end = _date(sched.get("project_start")), _date(sched.get("project_end"))
    target = _date(project.get("end_date"))
    days_late = (end - target).days if end and target else None
    days_early = -days_late if days_late is not None and days_late < 0 else None
    calendar_days = (end - start).days + 1 if start and end else None

    critical_ids = [int(i) for i in (sched.get("critical_task_ids") or []) if int(i) in by_id]
    critical_share = _round(len(critical_ids) / len(tasks), 3) if tasks else 0.0
    zero_float = [t["id"] for t in tasks if _num(t.get("total_float")) <= 0]
    total_hours = _round(sum(_num(t.get("estimate_hours")) for t in tasks), 1)

    per_task: dict[int, int] = {}
    for t in tasks:
        mid = t.get("assignee_member_id")
        if mid is not None:
            per_task[int(mid)] = per_task.get(int(mid), 0) + 1
    workload = []
    for m in capacity.get("members") or []:
        mid = int(m["member_id"])
        workload.append({
            "member_id": mid,
            "name": m.get("name") or f"member {mid}",
            "tasks": per_task.get(mid, 0),
            "assigned_hours": _round(_num(m.get("assigned_hours")), 1),
            "available_hours": _round(_num(m.get("available_hours")), 1),
            "utilization": _round(_num(m.get("utilization")), 3) if m.get("utilization") is not None else None,
            "is_overloaded": bool(m.get("is_overloaded")),
            "critical_tasks": [t["id"] for t in tasks
                               if t.get("assignee_member_id") is not None
                               and int(t["assignee_member_id"]) == mid and t["id"] in critical_ids],
        })
    workload.sort(key=lambda w: (-(w["utilization"] or 0), w["member_id"]))

    unassigned = [t["id"] for t in tasks if t.get("assignee_member_id") is None]
    no_estimate = [t["id"] for t in tasks if _num(t.get("estimate_hours")) <= 0]
    hours_per_day = {int(m["member_id"]): _num(m.get("hours_per_day"), DEFAULT_HOURS_PER_DAY)
                     for m in capacity.get("members") or []}
    under_allocated = []
    dependency_issues = []
    for t in tasks:
        est = _num(t.get("estimate_hours"))
        mid = t.get("assignee_member_id")
        capacity_hours = max(0, int(_num(t.get("duration_days")))) * hours_per_day.get(
            int(mid) if mid is not None else -1, DEFAULT_HOURS_PER_DAY)
        if est > capacity_hours + 0.01:
            under_allocated.append({"task_id": t["id"], "estimate_hours": _round(est, 1),
                                    "scheduled_capacity_hours": _round(capacity_hours, 1)})
        t_start = _date(t.get("start"))
        for dep in t.get("depends_on") or []:
            dep_task = by_id.get(int(dep))
            if dep_task is None:
                dependency_issues.append({"task_id": t["id"], "depends_on": int(dep),
                                          "issue": "missing dependency (not in this plan)"})
                continue
            dep_end = _date(dep_task.get("end"))
            if t_start and dep_end and t_start < dep_end:
                dependency_issues.append({"task_id": t["id"], "depends_on": int(dep),
                                          "issue": f"starts {t_start} before its dependency ends {dep_end}"})

    attention = []

    def flag(signal: str, severity: str, detail: str) -> None:
        attention.append({"signal": signal, "severity": severity, "detail": detail})

    if days_late is not None and days_late > 0:
        flag("deadline_exceeded", "critical" if days_late > 14 else "high",
             f"schedule ends {days_late} calendar day(s) after the target end date {target}")
    if unassigned:
        flag("unassigned_tasks", "high" if len(unassigned) > len(tasks) / 2 else "medium",
             f"{len(unassigned)} of {len(tasks)} tasks have no assignee")
    for w in workload:
        if w["is_overloaded"]:
            flag("overloaded_member", "high",
                 f"{w['name']} has {w['assigned_hours']} h against {w['available_hours']} h available "
                 f"({w['utilization'] or 0:.0%})")
    if dependency_issues:
        flag("dependency_timing", "high",
             f"{len(dependency_issues)} task(s) start before a dependency ends or reference a missing task")
    if no_estimate:
        flag("missing_estimates", "medium", f"{len(no_estimate)} task(s) have no hour estimate")
    if under_allocated:
        worst = max(under_allocated, key=lambda x: x["estimate_hours"] - x["scheduled_capacity_hours"])
        flag("under_allocated_task", "medium",
             f"{len(under_allocated)} task(s) schedule fewer hours than their estimate "
             f"(worst: task {worst['task_id']}, {worst['estimate_hours']} h estimate vs "
             f"{worst['scheduled_capacity_hours']} h scheduled)")
    if len(tasks) >= 4 and critical_share >= 0.75:
        flag("critical_path_concentration", "medium",
             f"{len(critical_ids)} of {len(tasks)} tasks are critical ({critical_share:.0%}): no slack for slips")
    if target is None:
        flag("no_target_end", "low", "the project has no target end date, so lateness cannot be measured")

    attention.sort(key=lambda a: (-{"critical": 3, "high": 2, "medium": 1, "low": 0}[a["severity"]],
                                  a["signal"]))
    attention = attention[:MAX_ATTENTION]

    overloaded = any(w["is_overloaded"] for w in workload)
    if days_late is not None and days_late > 0:
        signal = "late"
    elif critical_share >= 0.75 or overloaded or unassigned or dependency_issues:
        signal = "at_risk"
    else:
        signal = "on_track"

    chain = longest_chain(tasks, by_id)
    return {
        "schedule": {
            "start": sched.get("project_start"),
            "end": sched.get("project_end"),
            "target_end": project.get("end_date"),
            "days_late": days_late,
            "days_early": days_early,
            "working_days": sched.get("total_duration_days"),
            "calendar_days": calendar_days,
            "tasks": len(tasks),
            "critical_tasks": len(critical_ids),
            "critical_share": critical_share,
            "zero_float_tasks": len(zero_float),
            "total_estimate_hours": total_hours,
        },
        "delivery_signal": signal,
        "critical_chain": [
            {"id": tid, "title": by_id[tid]["title"], "duration_days": by_id[tid]["duration_days"],
             "assignee_member_id": by_id[tid].get("assignee_member_id")}
            for tid in chain
        ],
        "workload": workload,
        "unassigned_task_ids": unassigned,
        "dependency_issues": dependency_issues[:MAX_ATTENTION],
        "under_allocated_tasks": under_allocated[:MAX_ATTENTION],
        "attention": attention,
    }


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def confidence_cap(health: dict) -> dict:
    """How much evidence a review can honestly claim.

    A reviewer that cannot see the team, the target date or an owner for every task has no basis
    for "high" confidence. Deterministic, so the cap never drifts with the model's mood.
    """
    schedule = health.get("schedule") or {}
    reasons: list[str] = []
    tasks = int(schedule.get("tasks") or 0)
    if tasks == 0:
        reasons.append("no scheduled tasks were found")
    if not schedule.get("target_end"):
        reasons.append("the project has no target end date")
    unassigned = health.get("unassigned_task_ids") or []
    if unassigned:
        reasons.append(f"{len(unassigned)} task(s) have no assignee")
    if not health.get("workload"):
        reasons.append("no team capacity data was available")
    level = "low" if tasks == 0 else ("medium" if reasons else "high")
    return {"level": level, "reasons": reasons}


def cap_confidence(requested: str, cap: dict) -> tuple[str, list[str]]:
    """Lower a requested confidence to the evidence cap; return the final level and the notes."""
    cap_level = cap.get("level", "high")
    final = requested if CONFIDENCE_ORDER.get(requested, 1) <= CONFIDENCE_ORDER[cap_level] else cap_level
    notes = list(cap.get("reasons") or [])
    if final != requested:
        notes.insert(0, f"confidence lowered from {requested} to {final}: the plan data is incomplete")
    return final, notes
