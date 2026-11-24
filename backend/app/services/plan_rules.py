"""Deterministic plan checks (code, no LLM). They always run, whatever the agent does.

Each rule turns facts in a plan snapshot into a RiskDraft with real evidence. The agent can
add judgement on top, but it can't make these disappear, and it can't lower the verdict floor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.schemas.plan import RECOMMENDATION_FOR_VERDICT, SEVERITY_ORDER, EvidenceRef, RiskDraft

MAX_EVIDENCE = 8


@dataclass(frozen=True)
class RuleFinding:
    rule: str
    risk: RiskDraft

    @property
    def created_by(self) -> str:
        return f"rule:{self.rule}"


def _t(ids) -> list[EvidenceRef]:
    return [EvidenceRef(type="task", id=str(i)) for i in list(ids)[:MAX_EVIDENCE]]


def _names(snapshot: dict) -> dict[int, str]:
    return {m["member_id"]: (m.get("name") or f"member {m['member_id']}") for m in snapshot["capacity"]["members"]}


def run_rules(snapshot: dict) -> list[RuleFinding]:
    out: list[RuleFinding] = []
    tasks = snapshot.get("tasks", [])
    by_id = {t["id"]: t for t in tasks}
    names = _names(snapshot)
    sched = snapshot["schedule"]
    crit_ids = sched.get("critical_task_ids", [])

    # 1. deadline
    target = (snapshot.get("project") or {}).get("end_date")
    if target:
        over = (date.fromisoformat(sched["project_end"]) - date.fromisoformat(target)).days
        if over > 0:
            out.append(RuleFinding("deadline_exceeded", RiskDraft(
                title="Plan finishes after the project deadline",
                description=f"The schedule ends on {sched['project_end']}, {over} calendar days after the "
                            f"target end date {target}.",
                severity="critical" if over > 14 else "high", likelihood="high",
                mitigation="Cut scope, add capacity or move the deadline; the critical path is the place to start.",
                evidence_refs=_t(sched.get("critical_path") or [t["id"] for t in tasks]))))

    # 2. resource conflicts
    for c in snapshot.get("conflicts", [])[:10]:
        who = names.get(c["assignee_id"], f"member {c['assignee_id']}")
        refs = [EvidenceRef(type="conflict", id=c["key"]), EvidenceRef(type="member", id=str(c["assignee_id"]))]
        if c["kind"] == "overallocation":
            ratio = c.get("overload_ratio")
            out.append(RuleFinding("resource_conflict", RiskDraft(
                title=f"Resource conflict: {who} is overbooked from {c['start_date']}",
                description=f"{who} has overlapping tasks {c['start_date']} to {c['end_date']} "
                            f"({c.get('peak_load_hours_per_day')} h/day against "
                            f"{c.get('capacity_hours_per_day')} h/day capacity).",
                severity="high" if ratio is None or ratio >= 1.5 else "medium", likelihood="high",
                mitigation="Re-assign one of the tasks, stagger the start dates or add a person.",
                evidence_refs=refs + _t(c["task_ids"]))))
        else:
            out.append(RuleFinding("unavailable_assignee", RiskDraft(
                title=f"{who} is scheduled while unavailable from {c['start_date']}",
                description=f"Tasks {c['task_ids']} fall on {c['start_date']} to {c['end_date']}, "
                            f"outside {who}'s availability window.",
                severity="medium", likelihood="high",
                mitigation="Move the tasks or change the assignee.", evidence_refs=refs + _t(c["task_ids"]))))

    # 3. unassigned tasks
    unassigned = [t["id"] for t in tasks if t.get("assignee_member_id") is None]
    if unassigned:
        share = len(unassigned) / len(tasks)
        out.append(RuleFinding("unassigned_tasks", RiskDraft(
            title="Tasks without an assignee",
            description=f"{len(unassigned)} of {len(tasks)} tasks have no assignee, so resource conflicts "
                        "and workload can't be assessed for them.",
            severity="high" if share > 0.5 else "medium", likelihood="high",
            mitigation="Assign every task before approving the plan.", evidence_refs=_t(unassigned))))

    # 4. over-capacity members
    for m in snapshot["capacity"]["members"]:
        if m.get("is_overloaded"):
            theirs = [t["id"] for t in tasks if t.get("assignee_member_id") == m["member_id"]]
            util = m.get("utilization")
            out.append(RuleFinding("overloaded_member", RiskDraft(
                title=f"{names[m['member_id']]} is over capacity",
                description=f"Assigned {m['assigned_hours']} h against {m['available_hours']} h available"
                            + (f" ({util:.0%})." if util is not None else "."),
                severity="high", likelihood="high",
                mitigation="Rebalance the workload or extend the schedule.",
                evidence_refs=[EvidenceRef(type="member", id=str(m["member_id"]))] + _t(theirs))))

    # 5. no slack
    if len(tasks) >= 4 and len(crit_ids) / len(tasks) >= 0.75:
        out.append(RuleFinding("no_slack", RiskDraft(
            title="Almost no schedule slack",
            description=f"{len(crit_ids)} of {len(tasks)} tasks are on the critical path: any slip delays the end date.",
            severity="medium", likelihood="medium",
            mitigation="Parallelise work or add buffer on the critical tasks.", evidence_refs=_t(crit_ids))))

    # 6. single point of failure on the critical path
    crit_tasks = [by_id[i] for i in crit_ids if i in by_id and by_id[i]["duration_days"] > 0]
    total_days = sum(t["duration_days"] for t in crit_tasks)
    if len(crit_tasks) >= 3 and total_days:
        per: dict[int, int] = {}
        for t in crit_tasks:
            if t.get("assignee_member_id") is not None:
                per[t["assignee_member_id"]] = per.get(t["assignee_member_id"], 0) + t["duration_days"]
        if per:
            who_id, days = max(sorted(per.items()), key=lambda kv: kv[1])
            if days / total_days >= 0.6:
                theirs = [t["id"] for t in crit_tasks if t.get("assignee_member_id") == who_id]
                out.append(RuleFinding("single_point_of_failure", RiskDraft(
                    title=f"{names.get(who_id, f'member {who_id}')} owns most of the critical path",
                    description=f"{days} of {total_days} critical working days depend on one person.",
                    severity="medium", likelihood="medium",
                    mitigation="Pair someone with them or move part of the critical work.",
                    evidence_refs=[EvidenceRef(type="member", id=str(who_id))] + _t(theirs))))

    # 7. assignees outside the team
    unknown = snapshot["capacity"].get("unknown_assignees") or []
    if unknown:
        theirs = [t["id"] for t in tasks if t.get("assignee_member_id") in unknown]
        out.append(RuleFinding("unknown_assignee", RiskDraft(
            title="Tasks assigned to people outside the project team",
            description=f"Assignees {unknown} are not team members, so their capacity is unknown.",
            severity="medium", likelihood="high",
            mitigation="Add them to the team or re-assign the tasks.", evidence_refs=_t(theirs))))
    return out


def verdict_floor(findings: list[RuleFinding], snapshot: dict) -> str:
    """The lowest verdict the automatic checks allow (the agent may only go higher)."""
    worst = max((SEVERITY_ORDER[f.risk.severity] for f in findings), default=-1)
    if worst >= SEVERITY_ORDER["critical"]:
        return "blocked"
    if worst >= SEVERITY_ORDER["high"] or snapshot.get("conflicts"):
        return "needs_attention"
    return "ready"


def risk_verdict_floor(risks) -> str | None:
    """Consistency floor from the risks already stored for the draft (agent or rules).

    A reviewer who records a critical risk cannot then call the plan ready; the PM would have to
    decide which half of the review to believe. ``None`` = the register allows any verdict.
    """
    worst = max((SEVERITY_ORDER.get(str(r.get("severity")), -1) for r in risks), default=-1)
    if worst >= SEVERITY_ORDER["critical"]:
        return "blocked"
    if worst >= SEVERITY_ORDER["high"]:
        return "needs_attention"
    return None


VERDICT_ORDER = {"ready": 0, "needs_attention": 1, "blocked": 2}


def rules_review(findings: list[RuleFinding], snapshot: dict) -> dict:
    floor = verdict_floor(findings, snapshot)
    top = [f.risk.title for f in sorted(findings, key=lambda f: -SEVERITY_ORDER[f.risk.severity])][:5]
    summary = (
        f"Automatic checks found {len(findings)} issue(s); verdict: {floor}."
        if findings else "Automatic checks found no issues."
    )
    return {"verdict": floor, "summary": summary, "findings": top,
            "confidence": "medium" if findings else "high",
            "recommendation": RECOMMENDATION_FOR_VERDICT[floor], "open_questions": []}
