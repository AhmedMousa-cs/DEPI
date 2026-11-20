"""plan_versions service against the real database: versions, diff, draft-only risks."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import PlanVersion, Risk
from app.db.session import SessionLocal
from app.schemas.plan import EvidenceRef, PlanReviewDraft, RiskDraft
from app.services import plan_versions as pvs
from helpers import audit_rows, make_project, pm_headers
from plan_helpers import build_draft_snapshot, seed_plan_data


def setup_project():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    return pid, seed_plan_data(pid)


def new_draft(pid, **kw):
    _inputs, snap = build_draft_snapshot(pid, **kw)
    with SessionLocal() as s:
        pv = pvs.create_draft_version(s, project_id=pid, snapshot=snap, created_by="agent:agent3_schedule")
        s.commit()
        return pv.id, pv.version


def risk(task_id, title="Booking API is the bottleneck", severity="high"):
    return RiskDraft(title=title, description="API work blocks UI and tests.", severity=severity,
                     likelihood="medium", mitigation="Start the API first.",
                     evidence_refs=[EvidenceRef(type="task", id=str(task_id))])


def test_versions_increment_and_only_one_draft_exists():
    pid, _ = setup_project()
    _, v1 = new_draft(pid)
    pv2_id, v2 = new_draft(pid)
    assert (v1, v2) == (1, 2)
    with SessionLocal() as s:
        rows = {r.version: r for r in pvs.list_versions(s, pid)}
        assert rows[1].status == "superseded" and rows[2].status == "draft"
        assert pvs.current_draft(s, pid).id == pv2_id
    assert audit_rows("plan.draft_superseded")


def test_database_refuses_two_open_drafts():
    pid, _ = setup_project()
    new_draft(pid)
    _, snap = build_draft_snapshot(pid)
    with SessionLocal() as s:
        s.add(PlanVersion(project_id=pid, version=99, status="draft", snapshot=snap, created_by="x"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_new_versions_never_start_as_approved():
    pid, _ = setup_project()
    new_draft(pid)
    with SessionLocal() as s:
        assert pvs.current_approved(s, pid) is None
        assert pvs.get_version(s, pid, 1).approved_by is None


def test_diff_from_prev_is_stored_and_tracks_overrides():
    pid, data = setup_project()
    new_draft(pid)
    t_build = data["task_ids"][1]
    new_draft(pid, overrides=[{"task_id": t_build, "duration_days": 6}])  # v2: build API takes longer
    with SessionLocal() as s:
        v2 = pvs.get_version(s, pid, 2)
        assert v2.base_version == 1 and v2.diff_from_prev["initial"] is False
        ch = [c for c in v2.diff_from_prev["changes"] if c["id"] == t_build and c["field"] == "duration_days"]
        assert [(c["old"], c["new"]) for c in ch] == [(3, 6)]
        assert any(c["field"] == "project_end" for c in v2.diff_from_prev["changes"])
        first = pvs.get_version(s, pid, 1)
        assert first.base_version is None and first.diff_from_prev["initial"] is True


def test_three_versions_and_diff_between_any_two():
    pid, data = setup_project()
    new_draft(pid)
    new_draft(pid, overrides=[{"task_id": data["task_ids"][0], "duration_days": 5}])
    new_draft(pid, overrides=[{"task_id": data["task_ids"][0], "duration_days": 5},
                              {"task_id": data["task_ids"][2], "assignee_member_id": data["member_ids"][0]}])
    with SessionLocal() as s:
        d13 = pvs.diff_versions(s, pid, 1, 3)
        fields = {(c["id"], c["field"]) for c in d13["changes"] if c["scope"] == "task"}
        assert (data["task_ids"][0], "duration_days") in fields
        assert (data["task_ids"][2], "assignee_member_id") in fields
        assert [v.version for v in pvs.list_versions(s, pid)] == [1, 2, 3]
        with pytest.raises(pvs.PlanVersionError):
            pvs.diff_versions(s, pid, 1, 9)


def test_save_draft_risks_stores_drafts_and_mirrors_into_snapshot():
    pid, data = setup_project()
    pvid, _ = new_draft(pid)
    with SessionLocal() as s:
        out = pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, risks=[risk(data["task_ids"][1])],
                                   created_by="agent4_risk")
        s.commit()
        assert [r.status for r in out.saved] == ["draft"]
    with SessionLocal() as s:
        pv = s.get(PlanVersion, pvid)
        assert [r["title"] for r in pv.snapshot["risks"]] == ["Booking API is the bottleneck"]
        assert any(c["scope"] == "risk" for c in (pv.diff_from_prev or {}).get("changes", [])) or pv.base_version is None
    assert audit_rows("plan.risks_saved")


def test_risk_evidence_must_exist_in_this_project():
    pid, data = setup_project()
    pvid, _ = new_draft(pid)
    other = make_project(pm_headers())["id"]
    other_data = seed_plan_data(other)
    bad = [
        risk(999999, "Ghost task"),
        risk(other_data["task_ids"][0], "Task of another project"),
        RiskDraft(title="Ghost member", description="no such person", severity="low", likelihood="low",
                  evidence_refs=[EvidenceRef(type="member", id="424242")]),
        RiskDraft(title="Ghost conflict", description="no such conflict", severity="low", likelihood="low",
                  evidence_refs=[EvidenceRef(type="conflict", id="nope")]),
    ]
    with SessionLocal() as s:
        with pytest.raises(pvs.RiskValidationError) as e:
            pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, risks=bad, created_by="agent4_risk")
        assert len(e.value.errors) == 4
        s.rollback()
        assert s.scalar(select(Risk.id)) is None  # nothing saved, not even partially


def test_valid_member_requirement_and_conflict_evidence_is_accepted():
    pid, data = setup_project()
    pvid, _ = new_draft(pid, overrides=[{"task_id": data["task_ids"][2], "assignee_member_id": data["member_ids"][0]}])
    with SessionLocal() as s:
        snap = s.get(PlanVersion, pvid).snapshot
        key = snap["conflicts"][0]["key"]  # Ahmed now has two parallel tasks
        r = RiskDraft(title="Ahmed is double booked", description="Two parallel tasks.", severity="high",
                      likelihood="high", evidence_refs=[
                          EvidenceRef(type="conflict", id=key),
                          EvidenceRef(type="member", id=str(data["member_ids"][0])),
                          EvidenceRef(type="requirement", id=str(data["requirement_id"]))])
        assert len(pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, risks=[r],
                                        created_by="agent4_risk").saved) == 1


def test_duplicates_are_skipped_and_replace_is_idempotent():
    pid, data = setup_project()
    pvid, _ = new_draft(pid)
    t = data["task_ids"][0]
    with SessionLocal() as s:
        a = pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, created_by="agent4_risk",
                                 risks=[risk(t, "Same title"), risk(t, "same  TITLE")])
        assert len(a.saved) == 1 and a.skipped_duplicates == 1
        s.commit()
    with SessionLocal() as s:
        pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, created_by="agent4_risk",
                             risks=[risk(t, "Replacement")], replace=True)
        s.commit()
    with SessionLocal() as s:
        assert [r.title for r in pvs.risks_of(s, pvid)] == ["Replacement"]


def test_replace_only_touches_the_same_author():
    pid, data = setup_project()
    pvid, _ = new_draft(pid)
    t = data["task_ids"][0]
    with SessionLocal() as s:
        pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, risks=[risk(t, "From a rule")],
                             created_by="rule:deadline_exceeded")
        pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, risks=[risk(t, "From the agent")],
                             created_by="agent4_risk", replace=True)
        s.commit()
        assert {r.title for r in pvs.risks_of(s, pvid)} == {"From a rule", "From the agent"}


def test_only_drafts_can_be_changed():
    pid, data = setup_project()
    old_id, _ = new_draft(pid)
    new_draft(pid)  # supersedes the first
    t = data["task_ids"][0]
    with SessionLocal() as s:
        with pytest.raises(pvs.PlanVersionError, match="only a draft"):
            pvs.save_draft_risks(s, project_id=pid, plan_version_id=old_id, risks=[risk(t)], created_by="agent4_risk")
        with pytest.raises(pvs.PlanVersionError, match="only a draft"):
            pvs.save_review(s, project_id=pid, plan_version_id=old_id, source="agent4",
                            review=PlanReviewDraft(verdict="ready", summary="fine"))


def test_a_version_of_another_project_is_not_reachable():
    pid, data = setup_project()
    other = make_project(pm_headers())["id"]
    pvid, _ = new_draft(pid)
    with SessionLocal() as s:
        with pytest.raises(pvs.PlanVersionError, match="not found"):
            pvs.save_draft_risks(s, project_id=other, plan_version_id=pvid, risks=[risk(data["task_ids"][0])],
                                 created_by="agent4_risk")


def test_agents_cannot_smuggle_a_status_or_extra_fields():
    with pytest.raises(Exception):
        RiskDraft(title="x" * 5, description="y" * 5, severity="low", likelihood="low", status="open",
                  evidence_refs=[EvidenceRef(type="task", id="1")])
    with pytest.raises(Exception):  # evidence is mandatory
        RiskDraft(title="no evidence", description="none", severity="low", likelihood="low", evidence_refs=[])
    with pytest.raises(Exception):
        RiskDraft(title="bad severity", description="none", severity="catastrophic", likelihood="low",
                  evidence_refs=[EvidenceRef(type="task", id="1")])


def test_save_review_lands_in_the_snapshot():
    pid, _ = setup_project()
    pvid, _ = new_draft(pid)
    with SessionLocal() as s:
        pvs.save_review(s, project_id=pid, plan_version_id=pvid, source="agent4", run_id=None,
                        review=PlanReviewDraft(verdict="needs_attention", summary="Ahmed is overloaded.",
                                               findings=["  one  ", ""]))
        s.commit()
    with SessionLocal() as s:
        rv = s.get(PlanVersion, pvid).snapshot["review"]
        assert rv["verdict"] == "needs_attention" and rv["findings"] == ["one"] and rv["source"] == "agent4"


def test_inputs_fingerprint_detects_changes_that_matter():
    pid, data = setup_project()
    with SessionLocal() as s:
        base = pvs.inputs_fingerprint(pvs.load_plan_inputs(s, pid))
        assert base == pvs.inputs_fingerprint(pvs.load_plan_inputs(s, pid))
        t = s.get(__import__("app.db.models", fromlist=["Task"]).Task, data["task_ids"][0])
        t.title = "renamed"  # irrelevant for the schedule
        s.flush()
        assert pvs.inputs_fingerprint(pvs.load_plan_inputs(s, pid)) == base
        t.estimate_hours = 99
        s.flush()
        assert pvs.inputs_fingerprint(pvs.load_plan_inputs(s, pid)) != base


def test_only_approved_tasks_feed_the_plan():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed_plan_data(pid, review_status="draft")
    with SessionLocal() as s:
        assert pvs.load_plan_inputs(s, pid).tasks == []


def test_tables_can_be_truncated_with_the_project():
    pid, data = setup_project()
    pvid, _ = new_draft(pid)
    with SessionLocal() as s:
        pvs.save_draft_risks(s, project_id=pid, plan_version_id=pvid, risks=[risk(data["task_ids"][0])],
                             created_by="agent4_risk")
        s.commit()
        s.execute(text("DELETE FROM projects WHERE id = :i"), {"i": pid})
        s.commit()
        assert s.scalar(select(PlanVersion.id)) is None and s.scalar(select(Risk.id)) is None
