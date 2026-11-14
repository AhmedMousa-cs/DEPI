"""Pure tests for the snapshot diff (no DB)."""
from app.services.plan_versions import diff_snapshots


def snap(tasks, end="2026-11-20", total=5, crit=(), conflicts=(), risks=()):
    return {
        "schedule": {"project_end": end, "total_duration_days": total, "critical_path": list(crit)},
        "tasks": tasks,
        "conflicts": list(conflicts),
        "risks": list(risks),
    }


def task(id, title, days, **kw):
    base = {"id": id, "title": title, "duration_days": days, "start": "2026-11-15", "end": "2026-11-17",
            "assignee_member_id": None, "is_critical": False, "depends_on": []}
    base.update(kw)
    return base


def changes(d, **where):
    return [c for c in d["changes"] if all(c.get(k) == v for k, v in where.items())]


def test_spec_example_task_durations_and_end_date():
    v1 = snap([task(1, "Task A", 3), task(2, "Task B", 5)], end="2026-11-20")
    v2 = snap([task(1, "Task A", 4), task(2, "Task B", 3)], end="2026-11-19")
    d = diff_snapshots(v1, v2)
    assert not d["initial"]
    a = changes(d, label="Task A", field="duration_days")[0]
    b = changes(d, label="Task B", field="duration_days")[0]
    end = changes(d, scope="project", field="project_end")[0]
    assert (a["old"], a["new"]) == (3, 4)
    assert (b["old"], b["new"]) == (5, 3)
    assert (end["label"], end["old"], end["new"]) == ("End date", "2026-11-20", "2026-11-19")
    assert d["summary"]["tasks_changed"] == 2 and d["summary"]["end_date_shift_calendar_days"] == -1


def test_identical_snapshots_have_no_changes():
    s = snap([task(1, "A", 3)])
    d = diff_snapshots(s, s)
    assert d["changes"] == [] and d["summary"]["changes"] == 0


def test_first_version_is_initial():
    d = diff_snapshots(None, snap([task(1, "A", 3)]))
    assert d["initial"] is True and d["changes"] == []


def test_added_and_removed_tasks():
    d = diff_snapshots(snap([task(1, "A", 3), task(2, "B", 2)]), snap([task(1, "A", 3), task(3, "C", 1)]))
    assert [c["change"] for c in d["changes"] if c["scope"] == "task"] == ["removed", "added"]
    assert d["summary"]["tasks_added"] == 1 and d["summary"]["tasks_removed"] == 1


def test_assignee_dependency_and_critical_flag_changes():
    old = snap([task(2, "B", 2, assignee_member_id=1, depends_on=[1], is_critical=True)])
    new = snap([task(2, "B", 2, assignee_member_id=7, depends_on=[1, 3], is_critical=False)])
    fields = sorted(c["field"] for c in diff_snapshots(old, new)["changes"])
    assert fields == ["assignee_member_id", "depends_on", "is_critical"]


def test_critical_path_change():
    d = diff_snapshots(snap([], crit=[1, 2]), snap([], crit=[1, 3]))
    c = changes(d, field="critical_path")[0]
    assert (c["old"], c["new"]) == ([1, 2], [1, 3])


def test_conflicts_added_and_removed():
    old = snap([], conflicts=[{"key": "1|overallocation|2026-11-15|1,2"}])
    new = snap([], conflicts=[{"key": "2|overallocation|2026-11-16|3,4"}])
    s = diff_snapshots(old, new)["summary"]
    assert (s["conflicts_added"], s["conflicts_removed"]) == (1, 1)


def test_risks_added_removed_and_severity_changed_matched_by_normalised_title():
    old = snap([], risks=[{"title": "Deadline  at risk", "severity": "high"}, {"title": "Gone", "severity": "low"}])
    new = snap([], risks=[{"title": "deadline at risk", "severity": "critical"}, {"title": "Fresh", "severity": "medium"}])
    d = diff_snapshots(old, new)
    kinds = {(c["change"], c["label"]) for c in d["changes"] if c["scope"] == "risk"}
    assert kinds == {("changed", "deadline at risk"), ("removed", "Gone"), ("added", "Fresh")}


def test_diff_is_deterministic():
    old = snap([task(i, f"T{i}", i) for i in range(1, 6)])
    new = snap([task(i, f"T{i}", i + 1) for i in range(1, 6)])
    assert diff_snapshots(old, new) == diff_snapshots(old, new)
