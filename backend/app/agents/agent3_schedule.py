"""Agent 3 - Planning / scheduling.

Code-first (not a tool loop):
  1. resolve_durations : PM duration > estimate_hours (ceil) > LLM initial estimate
  2. compute_dates     : deterministic CPM            (code)
  3. detect_resource_conflicts + get_team_capacity    (code)
  4. explain_schedule  : LLM words only, template fallback

The LLM never produces dates, critical path, conflicts or capacity numbers.
It only (a) proposes initial durations that are validated/bounded by code and
(b) phrases an explanation of numbers that code already computed.
Nothing here writes approved data: `save_draft` is injected by the caller and
must only write status=draft rows (blueprint rule 2).
"""
from __future__ import annotations

import logging
import math
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, Sequence, TypeVar

from pydantic import BaseModel, Field

from app.schemas.scheduling import (
    ResourceConflict,
    ScheduleResult,
    TaskInput,
    TeamCapacityReport,
    TeamMemberInput,
    WorkCalendar,
)
from app.tools.compute_dates import compute_dates
from app.tools.resource_conflicts import detect_resource_conflicts
from app.tools.scheduling_errors import SchedulingError
from app.tools.team_capacity import get_team_capacity

log = logging.getLogger(__name__)

PROMPT_VERSION = "agent3_v1"
MAX_LLM_DURATION_DAYS = 60
_PROMPTS = Path(__file__).parent / "prompts"
T = TypeVar("T", bound=BaseModel)


class EstimationError(SchedulingError):
    pass


class StructuredLLM(Protocol):
    """Adapter over llm/structured.py::structured_call (retry + validation live there)."""

    def __call__(self, *, system_prompt: str, payload: dict[str, Any], schema: type[T]) -> T: ...


def load_prompt(name: str) -> str:
    return (_PROMPTS / f"{name}_v1.md").read_text(encoding="utf-8")


# ----------------------------------------------------------------------- estimation
class EstimateItem(BaseModel):
    task_id: str
    duration_days: int = Field(ge=1, le=MAX_LLM_DURATION_DAYS)
    rationale: str = ""


class EstimateBatch(BaseModel):
    estimates: list[EstimateItem]


class EstimateRecord(BaseModel):
    task_id: str
    duration_days: int
    source: Literal["pm", "hours", "llm"]
    rationale: str = ""


def resolve_durations(
    tasks: Sequence[TaskInput],
    llm: StructuredLLM | None = None,
    *,
    hours_per_day: float = 8.0,
    batch_size: int = 40,
) -> tuple[list[TaskInput], list[EstimateRecord]]:
    """Fill ``duration_days`` for every task. Returns (tasks, records), input order kept."""
    if hours_per_day <= 0:
        raise EstimationError("hours_per_day must be > 0")
    durations: dict[str, int] = {}
    records: dict[str, EstimateRecord] = {}
    need_llm: list[TaskInput] = []

    for t in tasks:
        if t.duration_days is not None:
            records[t.id] = EstimateRecord(task_id=t.id, duration_days=t.duration_days, source="pm")
        elif t.estimate_hours is not None:
            d = math.ceil(round(t.estimate_hours / hours_per_day, 6))
            records[t.id] = EstimateRecord(task_id=t.id, duration_days=d, source="hours")
        else:
            need_llm.append(t)
        if t.id in records:
            durations[t.id] = records[t.id].duration_days

    if need_llm:
        if llm is None:
            ids = ", ".join(t.id for t in need_llm)
            raise EstimationError(f"No duration/estimate_hours and no LLM available for tasks: {ids}")
        system_prompt = load_prompt("agent3_estimate")
        for i in range(0, len(need_llm), batch_size):
            chunk = need_llm[i : i + batch_size]
            payload = {
                "hours_per_day": hours_per_day,
                "tasks": [{"task_id": t.id, "title": t.title, "description": t.description} for t in chunk],
            }
            batch = llm(system_prompt=system_prompt, payload=payload, schema=EstimateBatch)
            got = {e.task_id: e for e in batch.estimates}  # unknown ids are ignored below
            missing = [t.id for t in chunk if t.id not in got]
            if missing:
                raise EstimationError("LLM returned no estimate for tasks: " + ", ".join(missing))
            for t in chunk:
                e = got[t.id]
                durations[t.id] = e.duration_days
                records[t.id] = EstimateRecord(
                    task_id=t.id, duration_days=e.duration_days, source="llm", rationale=e.rationale
                )

    resolved = [t.model_copy(update={"duration_days": durations[t.id]}) for t in tasks]
    return resolved, [records[t.id] for t in tasks]


# ---------------------------------------------------------------------- explanation
class ScheduleExplanation(BaseModel):
    summary: str
    critical_path: str
    conflicts: list[str] = []
    estimates: list[str] = []
    source: Literal["llm", "template"] = "llm"


def build_facts(
    schedule: ScheduleResult,
    conflicts: Sequence[ResourceConflict],
    capacity: TeamCapacityReport,
    estimates: Sequence[EstimateRecord],
    language: str = "ar-EG",
) -> dict[str, Any]:
    title = {t.id: (t.title or t.id) for t in schedule.tasks}
    by_id = {t.id: t for t in schedule.tasks}
    return {
        "language": language,
        "project_start": schedule.project_start.isoformat(),
        "project_end": schedule.project_end.isoformat(),
        "total_duration_working_days": schedule.total_duration_days,
        "critical_path": [
            {"id": i, "title": title[i], "start": by_id[i].start_date.isoformat(),
             "end": by_id[i].end_date.isoformat(), "duration_days": by_id[i].duration_days}
            for i in schedule.critical_path
        ],
        "float_days": {t.id: t.total_float for t in schedule.tasks if not t.is_critical},
        "conflicts": [
            {**c.model_dump(mode="json"), "task_titles": [title[i] for i in c.task_ids]}
            for c in conflicts
        ],
        "overloaded_members": [
            {"member_id": m.member_id, "name": m.name, "utilization": m.utilization}
            for m in capacity.members if m.is_overloaded
        ],
        "llm_estimates": [
            {"id": e.task_id, "title": title.get(e.task_id, e.task_id),
             "duration_days": e.duration_days, "rationale": e.rationale}
            for e in estimates if e.source == "llm"
        ],
    }


def template_explanation(facts: dict[str, Any]) -> ScheduleExplanation:
    """Deterministic fallback (also used when no LLM is configured)."""
    path = " → ".join(c["title"] for c in facts["critical_path"]) or "—"
    summary = (
        f"المشروع بيبدأ {facts['project_start']} ويخلص {facts['project_end']} "
        f"({facts['total_duration_working_days']} يوم شغل)."
    )
    conflicts = []
    for c in facts["conflicts"]:
        names = " + ".join(c["task_titles"])
        if c["kind"] == "overallocation":
            conflicts.append(
                f"{c['assignee_id']} محمّل فوق قدرته من {c['start_date']} لـ {c['end_date']} ({names}): "
                f"{c['peak_load_hours_per_day']} ساعة/يوم مقابل {c['capacity_hours_per_day']}."
            )
        else:
            conflicts.append(
                f"{names} متجدولة على {c['assignee_id']} من {c['start_date']} لـ {c['end_date']} "
                f"وهو مش متاح في الفترة دي."
            )
    estimates = [f"{e['title']}: {e['duration_days']} يوم (تقدير أولي) — {e['rationale']}".rstrip(" —")
                 for e in facts["llm_estimates"]]
    return ScheduleExplanation(
        summary=summary, critical_path=f"المسار الحرج: {path}",
        conflicts=conflicts, estimates=estimates, source="template",
    )


def explain_schedule(facts: dict[str, Any], llm: StructuredLLM | None = None) -> ScheduleExplanation:
    """Words only. Any LLM failure degrades to the template instead of failing the job."""
    if llm is None:
        return template_explanation(facts)
    try:
        out = llm(system_prompt=load_prompt("agent3_explain"), payload=facts, schema=ScheduleExplanation)
        return out.model_copy(update={"source": "llm"})
    except Exception:  # noqa: BLE001 - explanation is non-critical
        log.warning("agent3 explanation LLM call failed; using template", exc_info=True)
        return template_explanation(facts)


# --------------------------------------------------------------------------- runner
class Agent3Output(BaseModel):
    prompt_version: str = PROMPT_VERSION
    schedule: ScheduleResult
    conflicts: list[ResourceConflict]
    capacity: TeamCapacityReport
    estimates: list[EstimateRecord]
    explanation: ScheduleExplanation


def run_agent3(
    tasks: Sequence[TaskInput],
    members: Sequence[TeamMemberInput],
    project_start: date,
    *,
    calendar: WorkCalendar | None = None,
    llm: StructuredLLM | None = None,
    save_draft: Callable[[Agent3Output], None] | None = None,
    default_hours_per_day: float = 8.0,
) -> Agent3Output:
    cal = calendar or WorkCalendar()
    resolved, estimates = resolve_durations(tasks, llm, hours_per_day=default_hours_per_day)
    schedule = compute_dates(resolved, project_start, cal)
    conflicts = detect_resource_conflicts(schedule, members, cal, default_hours_per_day)
    capacity = get_team_capacity(members, schedule.project_start, schedule.project_end, cal, schedule)
    explanation = explain_schedule(build_facts(schedule, conflicts, capacity, estimates), llm)
    out = Agent3Output(
        schedule=schedule, conflicts=conflicts, capacity=capacity,
        estimates=estimates, explanation=explanation,
    )
    if save_draft is not None:
        save_draft(out)  # caller persists as status=draft only
    return out
