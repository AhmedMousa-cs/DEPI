"""Deterministic Agent-2 metrics for stories & tasks. No LLM judge: coverage, linkage and
sanity checks are computed directly from the golden plan."""
from __future__ import annotations

from pydantic import ValidationError

from app.schemas.planning import PlanningOutput, find_cycle

MIN_ESTIMATE_HOURS = 0.5
MAX_ESTIMATE_HOURS = 80.0
MAX_TASKS_PER_STORY = 8


def _terms_hit(text: str, terms: list[str]) -> bool:
    low = text.lower()
    hits = sum(1 for term in terms if term.lower() in low)
    return hits >= max(1, (len(terms) + 1) // 2)


def oracle_plan(expected: dict) -> dict:
    """The reference plan itself. Pipeline self-check only; says nothing about model quality."""
    return {
        "stories": [
            {
                "ref": s["ref"],
                "requirement_id": s["requirement_id"],
                "text": s["text"],
                "acceptance_criteria": list(s["acceptance_criteria"]),
                "confidence": 0.9,
                "order": s.get("order"),
            }
            for s in expected["stories"]
        ],
        "tasks": [
            {
                "ref": t["ref"],
                "story_ref": t["story_ref"],
                "title": t["title"],
                "estimate_hours": t["estimate_hours"],
                "depends_on": list(t.get("depends_on", [])),
                "confidence": 0.85,
                "order": t.get("order"),
                "is_critical": bool(t.get("is_critical")),
                "rationale": t.get("rationale"),
            }
            for t in expected["tasks"]
        ],
    }


def _priority(expected: dict, requirement_id: int) -> str | None:
    return next((r.get("priority") for r in expected["requirements"] if r["requirement_id"] == requirement_id), None)


def evaluate_plan(expected: dict, raw_output: dict) -> dict:
    errors: list[str] = []
    try:
        output = PlanningOutput.model_validate(raw_output)
        schema_valid = True
    except ValidationError as exc:
        output = None
        schema_valid = False
        errors.append(f"schema: {exc.errors()[0]['msg']}")

    expected_stories = expected["stories"]
    expected_tasks = expected["tasks"]
    requirement_ids = {r["requirement_id"] for r in expected["requirements"]}

    if output is None:
        failed = {k: False for k in ("link_integrity", "cycle_free", "estimate_sanity", "acceptance_criteria",
                                     "task_granularity", "ordering", "priority_first", "critical_path", "rationale")}
        return {
            "schema_valid": False,
            "story_coverage": 0.0,
            "story_recall": 0.0,
            "task_recall": 0.0,
            **failed,
            "total_hours": 0.0,
            "total_hours_ratio": 0.0,
            "details": {"errors": errors},
            "errors": errors,
            "passed": {"schema_valid": False, **failed},
        }

    stories = output.stories
    tasks = output.tasks
    story_by_ref = {s.ref: s for s in stories}
    story_ids = set(story_by_ref)
    story_requirements = {s.ref: s.requirement_id for s in stories}
    tasks_by_story: dict[str, list] = {ref: [] for ref in story_ids}
    for task in tasks:
        tasks_by_story.setdefault(task.story_ref, []).append(task)

    # coverage: every expected requirement has at least one story
    covered = {s.requirement_id for s in stories}
    story_coverage = round(len(covered & requirement_ids) / len(requirement_ids), 4) if requirement_ids else 0.0

    # story recall: expected story matched by requirement id + a majority of its match terms
    story_matched = 0
    matched_requirement_to_ref: dict[int, str] = {}
    for es in expected_stories:
        hit = next(
            (
                s
                for s in stories
                if s.requirement_id == es["requirement_id"] and _terms_hit(s.text, es.get("match_terms", []))
            ),
            None,
        )
        if hit:
            story_matched += 1
            matched_requirement_to_ref.setdefault(es["requirement_id"], hit.ref)
    story_recall = round(story_matched / len(expected_stories), 4) if expected_stories else 0.0

    # task recall: expected task matched by an actual task under a story for the same requirement
    task_matched = 0
    for et in expected_tasks:
        expected_story = next(s for s in expected_stories if s["ref"] == et["story_ref"])
        actual_ref = matched_requirement_to_ref.get(expected_story["requirement_id"])
        candidates = tasks_by_story.get(actual_ref, []) if actual_ref else []
        if any(_terms_hit(t.title, et.get("match_terms", [])) for t in candidates):
            task_matched += 1
    task_recall = round(task_matched / len(expected_tasks), 4) if expected_tasks else 0.0

    # structural integrity: links valid, every story has tasks, no duplicates
    link_errors: list[str] = []
    if any(t.story_ref not in story_ids for t in tasks):
        link_errors.append("task references a story that does not exist")
    if any(s.requirement_id not in requirement_ids for s in stories):
        link_errors.append("story references a requirement that is not approved")
    if any(not tasks_by_story.get(s.ref) for s in stories):
        link_errors.append("a story has no task")
    if len({(s.requirement_id, s.text.strip().lower()) for s in stories}) != len(stories):
        link_errors.append("duplicate story for the same requirement")
    link_integrity = not link_errors

    edges = {t.ref: list(t.depends_on) for t in tasks}
    cycle = find_cycle(edges)
    cycle_free = cycle is None and all(dep in {t.ref for t in tasks} for t in tasks for dep in t.depends_on)

    estimates = [float(t.estimate_hours) for t in tasks]
    distinct = len(set(estimates))
    estimate_sanity = (
        bool(estimates)
        and all(MIN_ESTIMATE_HOURS <= e <= MAX_ESTIMATE_HOURS for e in estimates)
        and (distinct > 1 or len(estimates) == 1)
    )

    acceptance_criteria = all(
        s.acceptance_criteria and all(5 <= len(c.strip()) <= 300 for c in s.acceptance_criteria) for s in stories
    )
    task_granularity = all(1 <= len(tasks_by_story.get(s.ref, [])) <= MAX_TASKS_PER_STORY for s in stories)

    total_hours = round(sum(estimates), 2)
    expected_hours = sum(float(t["estimate_hours"]) for t in expected_tasks)
    total_hours_ratio = round(abs(total_hours - expected_hours) / expected_hours, 4) if expected_hours else 1.0

    # professional ordering: every story and task carries an order, unique in its scope, and a
    # dependency is always earlier in the implied execution sequence than the task that needs it
    story_order = {s.ref: s.order for s in stories}
    order_by_ref = {t.ref: (story_order.get(t.story_ref), t.order) for t in tasks}
    ordering = (
        all(s.order is not None for s in stories)
        and all(t.order is not None for t in tasks)
        and len({s.order for s in stories}) == len(stories)
        and all(len({t.order for t in tasks_by_story.get(ref, [])}) == len(tasks_by_story.get(ref, [])) for ref in story_ids)
        and all(order_by_ref[t.ref] > order_by_ref[dep] for t in tasks for dep in t.depends_on)
    )

    # value first: high-priority requirements are planned before the rest
    high = [s.order for s in stories if s.order is not None and _priority(expected, s.requirement_id) == "high"]
    others = [s.order for s in stories if s.order is not None and _priority(expected, s.requirement_id) != "high"]
    priority_first = not others or (bool(high) and max(high) < min(others))

    # critical path: marked, but only on the backbone
    critical = [t for t in tasks if t.is_critical]
    critical_path = bool(tasks) and 1 <= len(critical) <= max(1, len(tasks) // 3)

    # every task explains why it exists
    rationale = bool(tasks) and all(t.rationale and 5 <= len(t.rationale.strip()) <= 200 for t in tasks)

    result = {
        "schema_valid": schema_valid,
        "story_coverage": story_coverage,
        "story_recall": story_recall,
        "task_recall": task_recall,
        "link_integrity": link_integrity,
        "cycle_free": cycle_free,
        "estimate_sanity": estimate_sanity,
        "acceptance_criteria": acceptance_criteria,
        "task_granularity": task_granularity,
        "ordering": ordering,
        "priority_first": priority_first,
        "critical_path": critical_path,
        "rationale": rationale,
        "total_hours": total_hours,
        "total_hours_ratio": total_hours_ratio,
        "details": {
            "requirements_without_story": sorted(requirement_ids - covered),
            "stories": len(stories),
            "tasks": len(tasks),
            "critical_tasks": len(critical),
            "unmatched_stories": [s["ref"] for s in expected_stories if not any(
                a.requirement_id == s["requirement_id"] and _terms_hit(a.text, s.get("match_terms", [])) for a in stories
            )],
            "link_errors": link_errors,
            "cycle": cycle,
            "expected_hours": expected_hours,
        },
        "errors": errors,
    }
    th = expected.get("thresholds", {})
    result["passed"] = {
        "story_coverage": result["story_coverage"] >= th.get("story_coverage", 0.9),
        "story_recall": result["story_recall"] >= th.get("story_recall", 0.7),
        "task_recall": result["task_recall"] >= th.get("task_recall", 0.6),
        "acceptance_criteria": result["acceptance_criteria"],
        "total_hours_ratio": result["total_hours_ratio"] <= th.get("total_hours_ratio", 0.5),
        "link_integrity": result["link_integrity"],
        "cycle_free": result["cycle_free"],
        "estimate_sanity": result["estimate_sanity"],
        "task_granularity": result["task_granularity"],
        "ordering": result["ordering"] == th.get("ordering", True),
        "priority_first": result["priority_first"] == th.get("priority_first", True),
        "critical_path": result["critical_path"] == th.get("critical_path", True),
        "rationale": result["rationale"] == th.get("rationale", True),
        "schema_valid": result["schema_valid"],
    }
    return result
