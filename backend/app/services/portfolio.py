"""PM command center (M7): portfolio health, cross-project resources and a risk register.

Everything here is DETERMINISTIC — computed from live data (requirements, tasks, schedules,
team capacity). No LLM. The PM opens one screen and knows what needs attention today:
which project is slipping, who is overbooked, and which risks to act on.

The PM's register includes open risks from published plan versions.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import (
    CoverageReport,
    PlanVersion,
    Project,
    Requirement,
    Risk,
    Task,
    TeamMember,
    UserStory,
)
from app.services.scheduling import latest_schedule_draft
from app.llm.runner import run_structured
from app.schemas.portfolio import PortfolioChatOutput

# A draft older than this is "aging": the pipeline is stalling on a PM decision.
AGING_DAYS = 3

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "agents" / "prompts" / "pm_assistant_v1.md"
ASSISTANT_AGENT = "pm_assistant"
ASSISTANT_PROMPT_VERSION = "pm_assistant_v1"

# Actions the dashboard can actually perform; anything else the model invents is dropped.
ALLOWED_ACTIONS = {
    "open_project", "review_drafts", "view_risks", "view_resources", "schedule",
}


def _accessible_projects(db: Session, user) -> list[Project]:
    """PMs see every project; leaders see only the projects they are members of."""
    stmt = select(Project).order_by(Project.id)
    if user.role != "PM":
        from app.db.models import ProjectMember

        stmt = stmt.join(ProjectMember, ProjectMember.project_id == Project.id).where(
            ProjectMember.user_id == user.id
        )
    return list(db.scalars(stmt))


def _hours_weighted_progress(tasks: list[Task]) -> int:
    """Completed estimate share over PM-approved tasks (0..100); 0 when there is nothing to sum."""
    approved = [t for t in tasks if t.review_status == "approved"]
    total = sum(t.estimate_hours for t in approved)
    if total <= 0:
        return 0
    done = sum(t.estimate_hours * (t.progress_pct / 100.0) for t in approved)
    return round(100 * done / total)


def _task_window(tasks: list[Task]) -> tuple[date | None, date | None]:
    dated = [t for t in tasks if t.start and t.end]
    if not dated:
        return None, None
    return min(t.start for t in dated), max(t.end for t in dated)


# --------------------------------------------------------------------------- overview


def project_card(db: Session, project: Project, today: date) -> dict:
    reqs = list(db.scalars(select(Requirement).where(Requirement.project_id == project.id)))
    stories = list(db.scalars(select(UserStory).where(UserStory.project_id == project.id)))
    tasks = list(db.scalars(select(Task).where(Task.project_id == project.id)))
    schedule = latest_schedule_draft(db, project.id)
    approved_reqs = sum(1 for r in reqs if r.status == "approved")
    approved_tasks = [t for t in tasks if t.review_status == "approved"]
    done_tasks = sum(1 for t in approved_tasks if t.status == "done")
    window_start, window_end = _task_window(approved_tasks)
    overdue = [
        t for t in approved_tasks
        if t.end and t.end < today and t.status != "done"
    ]
    pending = sum(1 for r in reqs if r.status == "draft") + sum(
        1 for s in stories if s.review_status == "draft"
    ) + sum(1 for t in tasks if t.review_status == "draft")

    # next deadline: the earliest future end among unfinished approved tasks, else the planned end
    upcoming = sorted(
        t.end for t in approved_tasks
        if t.end and t.end >= today and t.status != "done"
    )
    next_deadline = upcoming[0] if upcoming else project.end_date

    reasons: list[str] = []
    health = "green"
    if overdue:
        health = "red"
        reasons.append(f"{len(overdue)} task(s) past their end date and not done")
    if project.end_date and window_end and window_end > project.end_date:
        if health != "red":
            health = "yellow"
        reasons.append(f"the plan ends {window_end.isoformat()} after the planned end {project.end_date.isoformat()}")
    if pending:
        if health == "green":
            health = "yellow"
        reasons.append(f"{pending} draft(s) waiting for a PM decision")
    if approved_reqs and not approved_tasks and project.status not in ("setup", "scope_review"):
        if health == "green":
            health = "yellow"
        reasons.append("requirements are approved but no tasks are approved yet")
    if not reasons:
        reasons.append("on track")

    return {
        "project_id": project.id,
        "name": project.name,
        "status": project.status,
        "planned_start": project.start_date,
        "planned_end": project.end_date,
        "requirements": {"approved": approved_reqs, "draft": sum(1 for r in reqs if r.status == "draft")},
        "tasks": {
            "approved": len(approved_tasks),
            "done": done_tasks,
            "dated": sum(1 for t in approved_tasks if t.start and t.end),
        },
        "stories": {"approved": sum(1 for s in stories if s.review_status == "approved")},
        "progress_pct": _hours_weighted_progress(tasks),
        "schedule": {
            "window_start": window_start,
            "window_end": window_end,
            "latest_draft_id": schedule.id if schedule else None,
        },
        "next_deadline": next_deadline,
        "overdue_tasks": len(overdue),
        "overdue_titles": [t.title for t in overdue[:3]],
        "health": health,
        "health_reasons": reasons,
    }


def portfolio_overview(db: Session, user) -> dict:
    today = date.today()
    projects = _accessible_projects(db, user)
    cards = [project_card(db, p, today) for p in projects]
    counts = defaultdict(int)
    for c in cards:
        counts[c["health"]] += 1
    return {
        "projects": cards,
        "summary": {
            "total": len(cards),
            "green": counts["green"],
            "yellow": counts["yellow"],
            "red": counts["red"],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------- resources


def portfolio_resources(db: Session, user) -> dict:
    """Every team member across the PM's projects: capacity vs allocated hours, conflicts."""
    projects = _accessible_projects(db, user)
    project_names = {p.id: p.name for p in projects}
    members = list(db.scalars(
        select(TeamMember).where(TeamMember.project_id.in_([p.id for p in projects] or [0])).order_by(TeamMember.id)
    ))
    today = date.today()

    rows: list[dict] = []
    by_name: dict[str, list[dict]] = defaultdict(list)
    for m in members:
        pid = m.project_id
        tasks = list(db.scalars(select(Task).where(
            Task.project_id == pid,
            Task.assignee_member_id == m.id,
            Task.review_status == "approved",
        )))
        window_start, window_end = _task_window(tasks)
        allocated = sum(t.estimate_hours for t in tasks if t.start and t.end)
        weeks = 0.0
        if window_start and window_end:
            weeks = max((window_end - window_start).days + 1, 1) / 7.0
        per_week = allocated / weeks if weeks else 0.0
        capacity = float(m.capacity_hours_per_week or 40)
        utilization = round(100 * per_week / capacity) if capacity > 0 else 0
        row = {
            "member_id": m.id,
            "name": m.name,
            "role": m.role_title,
            "project_id": pid,
            "project_name": project_names.get(pid, f"#{pid}"),
            "capacity_hours_per_week": capacity,
            "allocated_hours": round(allocated, 1),
            "assigned_tasks": len(tasks),
            "weeks_window": round(weeks, 1),
            "hours_per_week": round(per_week, 1),
            "utilization_pct": utilization,
            "overallocated": utilization > 100,
            "window_start": window_start,
            "window_end": window_end,
        }
        rows.append(row)
        by_name[" ".join((m.name or "").lower().split())].append(row)

    # cross-project conflicts: the same person (same name) booked in 2+ projects with overlapping windows
    conflicts = []
    for key, group in by_name.items():
        if len(group) < 2 or len({r["project_id"] for r in group}) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["project_id"] == b["project_id"]:
                    continue
                if a["window_start"] and b["window_start"] and a["window_end"] and b["window_end"]:
                    if a["window_start"] <= b["window_end"] and b["window_start"] <= a["window_end"]:
                        conflicts.append({
                            "person": a["name"],
                            "projects": [a["project_name"], b["project_name"]],
                            "windows": [
                                [a["window_start"], a["window_end"]],
                                [b["window_start"], b["window_end"]],
                            ],
                        })

    total_capacity = sum(float(m.capacity_hours_per_week or 40) for m in members)
    total_per_week = sum(r["hours_per_week"] for r in rows)
    return {
        "members": rows,
        "conflicts": conflicts,
        "summary": {
            "members": len(rows),
            "overallocated": sum(1 for r in rows if r["overallocated"]),
            "total_capacity_hours_per_week": round(total_capacity, 1),
            "total_allocated_hours_per_week": round(total_per_week, 1),
            "utilization_pct": round(100 * total_per_week / total_capacity) if total_capacity else 0,
        },
    }


# --------------------------------------------------------------------------- risks


def portfolio_risks(db: Session, user) -> dict:
    """A deterministic risk register: every risk has evidence and a concrete next action."""
    today = date.today()
    projects = _accessible_projects(db, user)
    risks: list[dict] = []

    if user.role == "PM" and projects:
        project_names = {p.id: p.name for p in projects}
        published_risks = db.scalars(
            select(Risk).join(PlanVersion, (PlanVersion.id == Risk.plan_version_id)
                             & (PlanVersion.project_id == Risk.project_id))
            .where(Risk.project_id.in_(project_names), Risk.status == "open", PlanVersion.status == "approved")
            .order_by(Risk.id)
        )
        for risk in published_risks:
            risks.append({
                "type": "plan", "severity": risk.severity, "project_id": risk.project_id,
                "project_name": project_names[risk.project_id], "message": risk.title, "risk_id": risk.id,
                "suggestion": risk.mitigation or "Review the published plan risk with the team.",
            })

    for p in projects:
        card = project_card(db, p, today)
        for reason in card["health_reasons"]:
            if reason == "on track":
                continue
            severity = "high" if "past their end date" in reason or "after the planned end" in reason else "medium"
            risks.append({
                "type": "schedule" if severity == "high" or "draft" not in reason else "pipeline",
                "severity": severity,
                "project_id": p.id,
                "project_name": p.name,
                "message": reason,
                "suggestion": (
                    "Move the dates or cut scope, then re-run Agent 3."
                    if "end" in reason else
                    "Open the project and decide the pending drafts."
                ),
            })

        # aging drafts: the pipeline is stalled on a PM decision for too long
        draft_created = (
            list(db.scalars(select(Requirement.created_at).where(
                Requirement.project_id == p.id, Requirement.status == "draft")))
            + list(db.scalars(select(UserStory.created_at).where(
                UserStory.project_id == p.id, UserStory.review_status == "draft")))
            + list(db.scalars(select(Task.created_at).where(
                Task.project_id == p.id, Task.review_status == "draft")))
        )
        created_dates = [c.date() if isinstance(c, datetime) else c for c in draft_created if c]
        oldest = min(created_dates, default=None)
        if oldest is not None and (today - oldest).days > AGING_DAYS:
            risks.append({
                "type": "pipeline",
                "severity": "medium",
                "project_id": p.id,
                "project_name": p.name,
                "message": f"drafts have been waiting for a decision since {oldest.isoformat()} ({(today - oldest).days} days)",
                "suggestion": "Decide the pending drafts — every stalled draft delays the chain behind it.",
            })

        # gaps that keep coming back: the LLM report flagged missing areas and nobody decided
        report = db.scalar(
            select(CoverageReport).where(CoverageReport.project_id == p.id)
            .order_by(CoverageReport.id.desc()).limit(1)
        )
        if report and report.payload:
            missing = report.payload.get("missing", [])
            if len(missing) >= 5:
                risks.append({
                    "type": "scope",
                    "severity": "medium",
                    "project_id": p.id,
                    "project_name": p.name,
                    "message": f"{len(missing)} standard areas were never mentioned: {', '.join(missing[:4])}...",
                    "suggestion": "Ask the client about them before the scope becomes a surprise.",
                })

    # cross-project resource conflicts are portfolio risks by definition
    resources = portfolio_resources(db, user)
    for c in resources["conflicts"]:
        risks.append({
            "type": "resource",
            "severity": "high" if len(c["projects"]) > 2 else "medium",
            "project_id": None,
            "project_name": " / ".join(c["projects"]),
            "message": f"{c['person']} is booked in overlapping windows in {len(c['projects'])} projects.",
            "suggestion": "Rebalance the assignment or shift one project's window.",
        })
    for r in resources["members"]:
        if r["overallocated"]:
            risks.append({
                "type": "resource",
                "severity": "high",
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "message": f"{r['name']} is at {r['utilization_pct']}% utilization ({r['hours_per_week']}h/week of {r['capacity_hours_per_week']}h).",
                "suggestion": "Reassign tasks or extend the window.",
            })

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda r: (order.get(r["severity"], 4), r["project_name"]))
    counts = defaultdict(int)
    for r in risks:
        counts[r["severity"]] += 1
    return {
        "risks": risks,
        "summary": {"total": len(risks), "critical": counts["critical"], "high": counts["high"],
                    "medium": counts["medium"], "low": counts["low"]},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------- assistant chat


def _chat_snapshot(db: Session, user) -> dict:
    """The deterministic truth the assistant answers from: today's portfolio, compact."""
    overview = portfolio_overview(db, user)
    resources = portfolio_resources(db, user)
    risks = portfolio_risks(db, user)
    return {
        "portfolio": overview["summary"],
        "projects": [
            {
                "id": c["project_id"], "name": c["name"], "status": c["status"],
                "health": c["health"], "health_reasons": c["health_reasons"],
                "progress_pct": c["progress_pct"], "overdue_tasks": c["overdue_tasks"],
                "overdue_task_titles": c["overdue_titles"],
                "next_deadline": c["next_deadline"], "planned_end": c["planned_end"],
                "drafts_pending": c["requirements"]["draft"],
            }
            for c in overview["projects"]
        ],        "resources": {
            "summary": resources["summary"],
            "conflicts": resources["conflicts"],
            # only the notable people keep the payload small for the chat
            "overallocated": [
                {k: r[k] for k in ("name", "project_name", "utilization_pct",
                                   "hours_per_week", "capacity_hours_per_week")}
                for r in resources["members"] if r["overallocated"]
            ],
        },
        "risks": [
            {k: r[k] for k in ("type", "severity", "project_name", "message", "suggestion")}
            for r in risks["risks"]
        ],
    }


def portfolio_chat(db: Session, user, messages: list[dict]) -> dict:
    """One PM-assistant turn: deterministic snapshot + conversation -> reply + suggestions.

    The run is portfolio-level (project_id=None) and still goes through run_structured:
    daily budget, agent_runs, audit and Langfuse tracing all apply.
    """
    snapshot = _chat_snapshot(db, user)
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[-12:]
    ]
    payload = {
        "snapshot": snapshot,
        "conversation": history,
    }
    llm_messages = [
        {"role": "system", "content": _PROMPT_PATH.read_text(encoding="utf-8")},
        # the snapshot AND the conversation are untrusted data; the prompt says so explicitly
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]
    run = run_structured(
        project_id=None,
        job_id=None,
        agent=ASSISTANT_AGENT,
        prompt_version=ASSISTANT_PROMPT_VERSION,
        messages=llm_messages,
        schema=PortfolioChatOutput,
    )
    parsed: PortfolioChatOutput = run.parsed

    # --- sanitise: the model may only point at visible projects and UI-performable actions
    visible = {p.id for p in _accessible_projects(db, user)}
    suggestions = []
    for s in parsed.suggestions:
        if s.action not in ALLOWED_ACTIONS:
            continue
        if s.project_id is not None and s.project_id not in visible:
            continue
        suggestions.append({"title": s.title, "action": s.action, "project_id": s.project_id})
    suggestions = suggestions[:3]

    write_audit(
        db,
        actor=user.email,
        action="portfolio.assistant_chat",
        entity_type="portfolio",
        entity_id=None,
        after={"run_id": run.run_id, "prompt_version": ASSISTANT_PROMPT_VERSION,
               "suggestions": len(suggestions)},
    )
    return {
        "reply": parsed.reply,
        "suggestions": suggestions,
        "run_id": run.run_id,
        "trace_id": run.trace_id,
    }
