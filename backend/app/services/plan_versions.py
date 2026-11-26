"""Plan versions: snapshot, diff, draft creation and draft-only risk / review persistence.

Rules (Blueprint section 2):
* Agents write DRAFTS only. Nothing in this module can set a version to ``approved``:
  that happens only in app/orchestrator/plan_gate.py after a real PM decision.
* A version that is not a draft is immutable (risks, review and snapshot can't change).
* Every version keeps a full JSON snapshot; ``diff_from_prev`` is computed against the
  immediately preceding version number, and any two versions can be diffed on demand.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.db.models import PlanVersion, Project, Requirement, Risk, Task, TaskDependency, TeamMember
from app.schemas.plan import PlanReviewDraft, RiskDraft

SNAPSHOT_SCHEMA = 1
INPUTS_FINGERPRINT_VERSION = 2
TASK_DIFF_FIELDS = (
    ("duration_days", "Duration (working days)"),
    ("start", "Start"),
    ("end", "End"),
    ("assignee_member_id", "Assignee"),
    ("is_critical", "Critical"),
    ("depends_on", "Depends on"),
)


class PlanVersionError(ValueError):
    pass


class RiskValidationError(PlanVersionError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


# ----------------------------------------------------------------------------- inputs
@dataclass
class PlanInputs:
    project: dict
    tasks: list[dict]
    deps: dict[int, list[int]]
    members: list[dict]
    dangling: list[tuple[int, int]] = field(default_factory=list)  # (task, depends_on a non-approved task)


def load_plan_inputs(db: Session, project_id: int, *, lock: bool = False) -> PlanInputs:
    """The approved data a plan is built from (approved tasks, their dependencies, the team)."""
    project = db.get(Project, project_id)
    if project is None:
        raise PlanVersionError("project not found")
    member_query = select(TeamMember).where(TeamMember.project_id == project_id).order_by(TeamMember.id)
    task_query = select(Task).where(Task.project_id == project_id).order_by(Task.id)
    if lock:
        # The approval transaction already holds the parent project lock. Lock the team before
        # tasks (deleting a member can clear task assignments), then lock every project task
        # so a concurrently approved task cannot enter the scheduling inputs mid-approval.
        member_query = member_query.with_for_update(read=True, nowait=True).execution_options(populate_existing=True)
        task_query = task_query.with_for_update(nowait=True).execution_options(populate_existing=True)
    else:
        task_query = task_query.where(Task.review_status == "approved")
    members = list(db.scalars(member_query))
    tasks = [task for task in db.scalars(task_query) if task.review_status == "approved"]
    ids = {t.id for t in tasks}
    deps: dict[int, list[int]] = {t.id: [] for t in tasks}
    dangling: list[tuple[int, int]] = []
    if ids:
        dependency_query = select(TaskDependency).where(TaskDependency.task_id.in_(ids))
        if lock:
            dependency_query = dependency_query.order_by(TaskDependency.task_id, TaskDependency.depends_on_task_id)
            dependency_query = dependency_query.with_for_update(nowait=True).execution_options(populate_existing=True)
        for d in db.scalars(dependency_query):
            if d.depends_on_task_id in ids:
                deps[d.task_id].append(d.depends_on_task_id)
            else:
                dangling.append((d.task_id, d.depends_on_task_id))
    for v in deps.values():
        v.sort()
    return PlanInputs(
        project={
            "id": project.id,
            "name": project.name,
            "status": project.status,
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "end_date": project.end_date.isoformat() if project.end_date else None,
        },
        tasks=[
            {
                "id": t.id,
                "title": t.title,
                "estimate_hours": t.estimate_hours,
                "assignee_member_id": t.assignee_member_id,
                "story_id": t.story_id,
            }
            for t in tasks
        ],
        deps=deps,
        members=[
            {
                "id": m.id,
                "name": m.name,
                "capacity_hours_per_week": m.capacity_hours_per_week,
                "available_from": m.available_from.isoformat() if m.available_from else None,
                "available_to": m.available_to.isoformat() if m.available_to else None,
            }
            for m in members
        ],
        dangling=sorted(dangling),
    )


def inputs_fingerprint(inputs: PlanInputs, *, version: int = INPUTS_FINGERPRINT_VERSION) -> str:
    """Hash the schedule inputs. Version 1 is retained for drafts saved before date checking."""
    payload = {
        "tasks": [[t["id"], t["estimate_hours"], t["assignee_member_id"]] for t in inputs.tasks],
        "deps": sorted([k, sorted(v)] for k, v in inputs.deps.items()),
        "members": [
            [m["id"], m["capacity_hours_per_week"], m["available_from"], m["available_to"]]
            for m in inputs.members
        ],
    }
    if version == INPUTS_FINGERPRINT_VERSION:
        payload["project_dates"] = [inputs.project["start_date"], inputs.project["end_date"]]
        payload["dangling"] = sorted(inputs.dangling)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def snapshot_matches_inputs(snapshot: dict, inputs: PlanInputs) -> bool:
    version = snapshot.get("inputs_fingerprint_version", 1)
    if version == 1:
        # Legacy snapshots kept the original project dates, and Agent 3 never scheduled a
        # dangling dependency. Require both before relying on the legacy fingerprint.
        project = snapshot.get("project") or {}
        if inputs.dangling or any(
            key not in project or project[key] != inputs.project[key] for key in ("start_date", "end_date")
        ):
            return False
    elif version != INPUTS_FINGERPRINT_VERSION:
        return False
    return inputs_fingerprint(inputs, version=version) == snapshot.get("inputs_fingerprint")


# -------------------------------------------------------------------------- snapshot
def conflict_key(c: dict) -> str:
    return f"{c['assignee_id']}|{c['kind']}|{c['start_date']}|{','.join(str(i) for i in c['task_ids'])}"


def _int_or_none(v) -> int | None:
    return None if v is None else int(v)


def build_snapshot(
    *,
    inputs: PlanInputs,
    output,  # app.agents.agent3_schedule.Agent3Output (duck-typed to keep this module import-light)
    overrides: Sequence[dict] = (),
) -> dict:
    """JSON-safe snapshot of an Agent 3 result. Ids are DB ids (ints)."""
    sched = output.schedule
    tasks = []
    for t in sched.tasks:
        tid = int(t.id)
        tasks.append(
            {
                "id": tid,
                "title": t.title,
                "assignee_member_id": _int_or_none(t.assignee_id),
                "duration_days": t.duration_days,
                "estimate_hours": t.estimate_hours,
                "start": t.start_date.isoformat(),
                "end": t.end_date.isoformat(),
                "es": t.es,
                "ef": t.ef,
                "total_float": t.total_float,
                "is_critical": t.is_critical,
                "depends_on": sorted(inputs.deps.get(tid, [])),
            }
        )
    conflicts = []
    for c in output.conflicts:
        d = c.model_dump(mode="json")
        d["task_ids"] = [int(i) for i in d["task_ids"]]
        d["assignee_id"] = int(d["assignee_id"]) if str(d["assignee_id"]).isdigit() else d["assignee_id"]
        d["key"] = conflict_key(d)
        conflicts.append(d)
    cap = output.capacity.model_dump(mode="json")
    for m in cap["members"]:
        m["member_id"] = int(m["member_id"])
    cap["unknown_assignees"] = [int(i) if str(i).isdigit() else i for i in cap["unknown_assignees"]]
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "project": dict(inputs.project),
        "schedule": {
            "project_start": sched.project_start.isoformat(),
            "project_end": sched.project_end.isoformat(),
            "total_duration_days": sched.total_duration_days,
            "critical_path": [int(i) for i in sched.critical_path],
            "critical_task_ids": [int(i) for i in sched.critical_task_ids],
        },
        "tasks": tasks,
        "conflicts": conflicts,
        "capacity": cap,
        "estimates": [
            {**e.model_dump(mode="json"), "task_id": int(e.task_id)} for e in output.estimates
        ],
        "explanation": output.explanation.model_dump(mode="json"),
        "overrides": [dict(o) for o in overrides],
        "inputs_fingerprint_version": INPUTS_FINGERPRINT_VERSION,
        "inputs_fingerprint": inputs_fingerprint(inputs),
        "prompt_version": output.prompt_version,
        "risks": [],
        "review": None,
    }


# ------------------------------------------------------------------------------ diff
def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower())


def diff_snapshots(old: dict | None, new: dict) -> dict:
    """Pure, deterministic diff of two snapshots.

    ``changes`` entries: {scope, change(changed|added|removed), id, label, field, old, new}.
    """
    if old is None:
        return {"initial": True, "summary": {"changes": 0}, "changes": []}

    changes: list[dict] = []

    def add(scope, change, id_, label, field_, o, n):
        changes.append(
            {"scope": scope, "change": change, "id": id_, "label": label, "field": field_, "old": o, "new": n}
        )

    os_, ns_ = old["schedule"], new["schedule"]
    for key, label in (("project_end", "End date"), ("total_duration_days", "Total duration (working days)")):
        if os_.get(key) != ns_.get(key):
            add("project", "changed", None, label, key, os_.get(key), ns_.get(key))
    if os_.get("critical_path") != ns_.get("critical_path"):
        add("project", "changed", None, "Critical path", "critical_path",
            os_.get("critical_path"), ns_.get("critical_path"))

    o_tasks = {t["id"]: t for t in old.get("tasks", [])}
    n_tasks = {t["id"]: t for t in new.get("tasks", [])}
    tasks_changed = 0
    for tid in sorted(set(o_tasks) | set(n_tasks)):
        o, n = o_tasks.get(tid), n_tasks.get(tid)
        if o is None:
            add("task", "added", tid, n["title"], None, None, None)
        elif n is None:
            add("task", "removed", tid, o["title"], None, None, None)
        else:
            before = len(changes)
            for f, _label in TASK_DIFF_FIELDS:
                if o.get(f) != n.get(f):
                    add("task", "changed", tid, n["title"], f, o.get(f), n.get(f))
            tasks_changed += len(changes) > before

    o_conf = {c["key"]: c for c in old.get("conflicts", [])}
    n_conf = {c["key"]: c for c in new.get("conflicts", [])}
    for k in sorted(set(o_conf) | set(n_conf)):
        if k not in o_conf:
            add("conflict", "added", k, k, None, None, None)
        elif k not in n_conf:
            add("conflict", "removed", k, k, None, None, None)

    o_risk = {_norm_title(r["title"]): r for r in old.get("risks", [])}
    n_risk = {_norm_title(r["title"]): r for r in new.get("risks", [])}
    for k in sorted(set(o_risk) | set(n_risk)):
        if k not in o_risk:
            add("risk", "added", None, n_risk[k]["title"], None, None, n_risk[k]["severity"])
        elif k not in n_risk:
            add("risk", "removed", None, o_risk[k]["title"], None, o_risk[k]["severity"], None)
        elif o_risk[k]["severity"] != n_risk[k]["severity"]:
            add("risk", "changed", None, n_risk[k]["title"], "severity",
                o_risk[k]["severity"], n_risk[k]["severity"])

    shift = None
    try:
        shift = (date.fromisoformat(ns_["project_end"]) - date.fromisoformat(os_["project_end"])).days
    except (KeyError, ValueError):
        pass
    summary = {
        "changes": len(changes),
        "tasks_changed": tasks_changed,
        "tasks_added": sum(1 for c in changes if c["scope"] == "task" and c["change"] == "added"),
        "tasks_removed": sum(1 for c in changes if c["scope"] == "task" and c["change"] == "removed"),
        "conflicts_added": sum(1 for c in changes if c["scope"] == "conflict" and c["change"] == "added"),
        "conflicts_removed": sum(1 for c in changes if c["scope"] == "conflict" and c["change"] == "removed"),
        "risks_added": sum(1 for c in changes if c["scope"] == "risk" and c["change"] == "added"),
        "risks_removed": sum(1 for c in changes if c["scope"] == "risk" and c["change"] == "removed"),
        "end_date_shift_calendar_days": shift,
    }
    return {"initial": False, "summary": summary, "changes": changes}


# ---------------------------------------------------------------------------- queries
def get_version(db: Session, project_id: int, version: int, *, lock: bool = False,
                nowait: bool = False) -> PlanVersion | None:
    stmt = select(PlanVersion).where(PlanVersion.project_id == project_id, PlanVersion.version == version)
    if lock:
        stmt = stmt.with_for_update(nowait=nowait).execution_options(populate_existing=True)
    return db.scalar(stmt)


def latest_version(db: Session, project_id: int) -> PlanVersion | None:
    return db.scalar(
        select(PlanVersion).where(PlanVersion.project_id == project_id).order_by(PlanVersion.version.desc()).limit(1)
    )


def current_draft(db: Session, project_id: int) -> PlanVersion | None:
    return db.scalar(select(PlanVersion).where(PlanVersion.project_id == project_id, PlanVersion.status == "draft"))


def current_approved(db: Session, project_id: int) -> PlanVersion | None:
    return db.scalar(
        select(PlanVersion).where(PlanVersion.project_id == project_id, PlanVersion.status == "approved")
        .order_by(PlanVersion.version.desc()).limit(1)
    )


def list_versions(db: Session, project_id: int) -> list[PlanVersion]:
    return list(
        db.scalars(select(PlanVersion).where(PlanVersion.project_id == project_id).order_by(PlanVersion.version))
    )


def diff_versions(db: Session, project_id: int, from_version: int, to_version: int) -> dict:
    a, b = get_version(db, project_id, from_version), get_version(db, project_id, to_version)
    if a is None or b is None:
        raise PlanVersionError("version not found")
    return {"from_version": from_version, "to_version": to_version, **diff_snapshots(a.snapshot, b.snapshot)}


def risks_of(db: Session, plan_version_id: int, *, include_dismissed: bool = False) -> list[Risk]:
    stmt = select(Risk).where(Risk.plan_version_id == plan_version_id).order_by(Risk.id)
    if not include_dismissed:
        stmt = stmt.where(Risk.status != "dismissed")
    return list(db.scalars(stmt))


# ----------------------------------------------------------------------------- writes
def _require_draft(pv: PlanVersion) -> None:
    if pv.status != "draft":
        raise PlanVersionError(f"plan version {pv.version} is '{pv.status}'; only a draft can be changed")


def create_draft_version(
    db: Session, *, project_id: int, snapshot: dict, created_by: str, run_id: int | None = None
) -> PlanVersion:
    """New draft = next version number. An older open draft is superseded. Never approved."""
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise PlanVersionError("project not found")
    prev = latest_version(db, project_id)
    old_draft = current_draft(db, project_id)
    if old_draft is not None:
        old_draft.status = "superseded"
        db.execute(
            update(Risk).where(Risk.plan_version_id == old_draft.id, Risk.status == "draft").values(status="dismissed")
        )
        write_audit(db, created_by, "plan.draft_superseded", "plan_version", old_draft.id,
                    before={"status": "draft"}, after={"status": "superseded", "by_version": (prev.version + 1)})
        db.flush()
    number = (prev.version if prev else 0) + 1
    pv = PlanVersion(
        project_id=project_id,
        version=number,
        status="draft",
        snapshot=snapshot,
        base_version=prev.version if prev else None,
        diff_from_prev=diff_snapshots(prev.snapshot if prev else None, snapshot),
        created_by=created_by,
        run_id=run_id,
    )
    db.add(pv)
    db.flush()
    write_audit(db, created_by, "plan.draft_created", "plan_version", pv.id,
                after={"project_id": project_id, "version": number, "base_version": pv.base_version,
                       "tasks": len(snapshot.get("tasks", []))})
    return pv


def _validate_evidence(db: Session, project_id: int, snapshot: dict, risks: Sequence[RiskDraft]) -> list[str]:
    task_ids = {t["id"] for t in snapshot.get("tasks", [])}
    conflict_keys = {c["key"] for c in snapshot.get("conflicts", [])}
    member_ids = set(db.scalars(select(TeamMember.id).where(TeamMember.project_id == project_id)))
    req_ids = set(db.scalars(select(Requirement.id).where(Requirement.project_id == project_id)))
    errors: list[str] = []
    for r in risks:
        for e in r.evidence_refs:
            ok = False
            if e.type == "conflict":
                ok = e.id in conflict_keys
            elif e.id.isdigit():
                n = int(e.id)
                ok = n in {"task": task_ids, "member": member_ids, "requirement": req_ids}[e.type]
            if not ok:
                errors.append(f"risk {r.title[:50]!r}: evidence {e.type}:{e.id} does not exist in this plan/project")
    return errors


def _risk_row(r: Risk) -> dict:
    return {
        "id": r.id, "title": r.title, "description": r.description, "severity": r.severity,
        "likelihood": r.likelihood, "mitigation": r.mitigation, "evidence_refs": r.evidence_refs,
        "status": r.status, "created_by": r.created_by,
    }


def _refresh_snapshot_risks(db: Session, pv: PlanVersion) -> None:
    db.flush()
    snap = dict(pv.snapshot)
    snap["risks"] = [_risk_row(r) for r in risks_of(db, pv.id)]
    pv.snapshot = snap
    base = get_version(db, pv.project_id, pv.base_version) if pv.base_version else None
    pv.diff_from_prev = diff_snapshots(base.snapshot if base else None, snap)


def clear_draft_risks(db: Session, *, plan_version_id: int, created_by_prefix: str) -> int:
    pv = db.get(PlanVersion, plan_version_id, with_for_update=True)
    if pv is None:
        raise PlanVersionError("plan version not found")
    _require_draft(pv)
    rows = [r for r in risks_of(db, pv.id, include_dismissed=True) if r.created_by.startswith(created_by_prefix)]
    for r in rows:
        db.delete(r)
    db.flush()
    return len(rows)


@dataclass(frozen=True)
class SavedRisks:
    saved: list[Risk]
    skipped_duplicates: int


def save_draft_risks(
    db: Session,
    *,
    project_id: int,
    plan_version_id: int,
    risks: Sequence[RiskDraft],
    created_by: str,
    run_id: int | None = None,
    actor: str | None = None,
    replace: bool = False,
) -> SavedRisks:
    """Attach DRAFT risks to a draft plan version. Evidence must point at real items.

    ``replace=True`` first removes this author's earlier risks on the version (idempotent re-run).
    """
    pv = db.get(PlanVersion, plan_version_id, with_for_update=True)
    if pv is None or pv.project_id != project_id:
        raise PlanVersionError("plan version not found in this project")
    _require_draft(pv)
    errors = _validate_evidence(db, project_id, pv.snapshot, risks)
    if errors:
        raise RiskValidationError(errors)
    if replace:
        clear_draft_risks(db, plan_version_id=plan_version_id, created_by_prefix=created_by)
    seen = {_norm_title(r.title) for r in risks_of(db, pv.id)}
    saved: list[Risk] = []
    skipped = 0
    for r in risks:
        key = _norm_title(r.title)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        row = Risk(
            project_id=project_id, plan_version_id=pv.id, title=r.title, description=r.description,
            severity=r.severity, likelihood=r.likelihood, mitigation=r.mitigation or None,
            evidence_refs=[e.model_dump() for e in r.evidence_refs], status="draft",
            created_by=created_by, run_id=run_id,
        )
        db.add(row)
        saved.append(row)
    _refresh_snapshot_risks(db, pv)
    write_audit(db, actor or f"agent:{created_by}", "plan.risks_saved", "plan_version", pv.id,
                after={"version": pv.version, "saved": len(saved), "skipped_duplicates": skipped,
                       "created_by": created_by})
    return SavedRisks(saved=saved, skipped_duplicates=skipped)


def save_review(
    db: Session,
    *,
    project_id: int,
    plan_version_id: int,
    review: PlanReviewDraft,
    source: str,
    run_id: int | None = None,
    actor: str | None = None,
) -> PlanVersion:
    pv = db.get(PlanVersion, plan_version_id, with_for_update=True)
    if pv is None or pv.project_id != project_id:
        raise PlanVersionError("plan version not found in this project")
    _require_draft(pv)
    snap = dict(pv.snapshot)
    snap["review"] = {
        **review.model_dump(),
        "source": source,
        "run_id": run_id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    pv.snapshot = snap
    write_audit(db, actor or f"agent:{source}", "plan.review_saved", "plan_version", pv.id,
                after={"version": pv.version, "verdict": review.verdict, "source": source})
    return pv
