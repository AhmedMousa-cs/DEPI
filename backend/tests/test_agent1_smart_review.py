"""M4.1: smart extraction (module + priority + clarify question), bulk review, coverage gaps."""
import pytest
from sqlalchemy import select

from app.db.models import Decision, Requirement, Source
from app.db.session import SessionLocal
from app.schemas.extraction import ExtractedRequirement
from app.services.quotes import verify_requirements
from app.services.requirements import save_draft_requirements
from helpers import audit_rows, client, make_project, pm_headers, tl_headers

from test_requirements_review import SOURCE_TEXT, seed_requirements  # reuse the same seeding


# ------------------------------------------------------------------ extraction schema (M4.1)
def test_new_fields_round_trip_through_the_schema():
    item = ExtractedRequirement(
        type="functional", text="Students can view past bookings.", source_id=1,
        exact_quote="الطالب يشوف تاريخ حجوزاته القديمة.", confidence=0.9,
        module="  Bookings   history ", priority="medium", clarify_question="Should old bookings be editable?",
    )
    assert item.module == "Bookings history"  # whitespace collapsed
    assert item.priority == "medium"

    decision = ExtractedRequirement(
        type="decision", text="Open question: payment method unclear.", source_id=1,
        exact_quote="we did not agree on payments yet", confidence=0.4, priority="high",
    )
    assert decision.priority is None  # a decision item never carries execution priority


def test_new_fields_are_validated():
    with pytest.raises(Exception):
        ExtractedRequirement(
            type="functional", text="x", source_id=1, exact_quote="abcdefgh",
            confidence=0.9, module="A",  # too short
        )
    with pytest.raises(Exception):
        ExtractedRequirement(
            type="functional", text="x", source_id=1, exact_quote="abcdefgh",
            confidence=0.9, priority="urgent",  # not an allowed value
        )


def test_save_draft_requirements_persists_the_new_fields():
    items = [
        ExtractedRequirement(
            type="functional", text="Customers can pay by card.", source_id=1,
            exact_quote="Payment by card online", confidence=0.7,
            module="Payments", priority="high", clarify_question="Which card providers exactly?",
        ),
    ]
    project_id = make_project(pm_headers())["id"]
    with SessionLocal() as s:
        src = Source(project_id=project_id, kind="note", title="t", content_text=SOURCE_TEXT, phase="initial")
        s.add(src)
        s.commit()
        verified = verify_requirements(items, {src.id: SOURCE_TEXT})
        rows = save_draft_requirements(s, project_id=project_id, items=verified)
        s.commit()
        assert len(rows) == 1
        assert rows[0].module == "Payments"
        assert rows[0].priority == "high"
        assert rows[0].clarify_question == "Which card providers exactly?"


# ------------------------------------------------------------------ bulk PM decision
def test_bulk_decision_approves_many_in_one_call():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    ids = seed_requirements(pid, n=3)

    r = client.post(f"/projects/{pid}/requirements/decision", json={"ids": ids, "decision": "approved"}, headers=pm)
    assert r.status_code == 200, r.text
    assert [row["id"] for row in r.json()] == ids
    assert all(row["status"] == "approved" for row in r.json())

    with SessionLocal() as s:
        rows = list(s.scalars(select(Requirement).where(Requirement.project_id == pid)))
        assert all(row.status == "approved" and row.locked_by_pm for row in rows)
        decisions = list(s.scalars(select(Decision).where(Decision.project_id == pid)))
        assert len(decisions) == 3
    audit = audit_rows("requirement.approved")
    assert len(audit) == 3 and all(a.after.get("bulk") is True for a in audit)


def test_bulk_reject_requires_a_comment():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    ids = seed_requirements(pid, n=2)
    r = client.post(f"/projects/{pid}/requirements/decision", json={"ids": ids, "decision": "rejected", "comment": ""}, headers=pm)
    assert r.status_code == 422
    r = client.post(f"/projects/{pid}/requirements/decision", json={"ids": ids, "decision": "rejected", "comment": "out of scope"}, headers=pm)
    assert r.status_code == 200 and all(row["status"] == "rejected" for row in r.json())


def test_bulk_decision_is_all_or_nothing_and_project_scoped():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    other_pid = make_project(pm)["id"]
    ids = seed_requirements(pid, n=2)
    other_ids = seed_requirements(other_pid, n=1)

    # one unknown id -> nothing is decided (single transaction)
    r = client.post(f"/projects/{pid}/requirements/decision", json={"ids": ids + [9999], "decision": "approved"}, headers=pm)
    assert r.status_code == 404 and "9999" in r.json()["detail"]
    with SessionLocal() as s:
        rows = list(s.scalars(select(Requirement).where(Requirement.project_id == pid)))
        assert all(row.status == "draft" for row in rows)

    # an id from ANOTHER project is also a 404, not a cross-project write
    r = client.post(f"/projects/{pid}/requirements/decision", json={"ids": other_ids, "decision": "approved"}, headers=pm)
    assert r.status_code == 404
    with SessionLocal() as s:
        row = s.get(Requirement, other_ids[0])
        assert row.status == "draft"

    # the leader cannot bulk-decide either
    r = client.post(f"/projects/{pid}/requirements/decision", json={"ids": ids, "decision": "approved"}, headers=tl_headers())
    assert r.status_code == 403


def test_single_decision_audit_snapshot_includes_the_module():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    ids = seed_requirements(pid, n=1)
    with SessionLocal() as s:
        s.get(Requirement, ids[0]).module = "Bookings"
        s.commit()

    r = client.post(f"/projects/{pid}/requirements/{ids[0]}/decision", json={"decision": "approved"}, headers=pm)
    assert r.status_code == 200
    assert r.json()["module"] == "Bookings"
    assert audit_rows("requirement.approved")[0].before.get("module") == "Bookings"


# ------------------------------------------------------------------ coverage gaps
def seed_gap_requirements(pid: int) -> list[int]:
    """Auth + payments mentioned (EN + AR); reporting/notifications/etc. never."""
    texts = [
        ("Customers must register and sign in.", "Customers must register and sign in.", "Authentication"),
        ("Payment by card online or cash on delivery.", "Payment by card online or cash on delivery.", "Payments"),
        ("العميل يدفع أونلاين ببطاقة.", "العميل يدفع أونلاين ببطاقة.", None),
    ]
    with SessionLocal() as s:
        src = Source(project_id=pid, kind="note", title="t", content_text=SOURCE_TEXT, phase="initial")
        s.add(src)
        s.flush()
        rows = []
        for text, quote, module in texts:
            rows.append(Requirement(
                project_id=pid, type="functional", text=text, source_id=src.id,
                quote=quote, quote_verified=True, confidence=0.9, module=module,
                status="draft", created_by_agent="agent1_extract",
            ))
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def test_gaps_report_covers_and_misses_the_right_areas():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    seed_gap_requirements(pid)

    r = client.get(f"/projects/{pid}/requirements/gaps", headers=pm)
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is False
    areas = {a["area"]: a for a in body["areas"]}
    assert areas["Authentication & accounts"]["covered"] is True
    assert areas["Payments & billing"]["covered"] is True  # matched via the Arabic row too
    assert areas["Payments & billing"]["matched_ids"] == [2, 3]
    assert areas["Data & reporting"]["covered"] is False
    assert areas["Notifications & communication"]["covered"] is False
    assert "Data & reporting" in body["missing"] and "Authentication & accounts" not in body["missing"]
    assert body["missing_count"] >= 8 and body["covered_count"] == 2


def test_gaps_ignore_rejected_requirements():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    ids = seed_gap_requirements(pid)
    client.post(f"/projects/{pid}/requirements/{ids[0]}/decision", json={"decision": "rejected", "comment": "no"}, headers=pm)

    body = client.get(f"/projects/{pid}/requirements/gaps", headers=pm).json()
    areas = {a["area"]: a for a in body["areas"]}
    assert areas["Authentication & accounts"]["covered"] is False  # rejected work no longer covers it


def test_gaps_empty_project():
    pm = pm_headers()
    pid = make_project(pm)["id"]
    body = client.get(f"/projects/{pid}/requirements/gaps", headers=pm).json()
    assert body["empty"] is True and body["areas"] == [] and body["llm"] is None
