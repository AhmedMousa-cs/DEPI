import random

import pytest
from sqlalchemy import select

from app.db.models import AuditLog
from app.db.session import SessionLocal
from app.orchestrator.checkpoints import memory_checkpointer, postgres_checkpointer
from app.orchestrator.decisions import InvalidDecision, parse_decision
from app.orchestrator.graph import (
    AlreadyWaiting,
    AuditBusinessWriter,
    GraphRunner,
    NotWaitingForDecision,
)
from app.orchestrator.state import ALLOWED_STATE_KEYS, make_thread_id


class RecordingWriter:
    def __init__(self):
        self.calls = []

    def write(self, **kw):
        self.calls.append(kw)
        return True


def approve(by=7, comment=""):
    return {"decision": "approved", "comment": comment, "decided_by": by}


def make(writer=None, saver=None):
    return GraphRunner(saver or memory_checkpointer(), writer or RecordingWriter())


def test_thread_id_is_deterministic():
    assert make_thread_id(7) == "project:7"
    for bad in (0, -1, "7", None, True):
        with pytest.raises(ValueError):
            make_thread_id(bad)


def test_graph_reaches_interrupt_without_business_write():
    w = RecordingWriter()
    r = make(w)
    st = r.start(1, "Build the booking page")
    assert st["state"] == "waiting_for_pm"
    assert st["thread_id"] == "project:1"
    assert st["interrupt"]["kind"] == "pm_approval"
    assert st["values"]["draft"].startswith("DRAFT")
    assert st["values"]["approval_status"] == "pending"
    assert w.calls == []


def test_approved_resume_writes_once():
    w = RecordingWriter()
    r = make(w)
    r.start(2, "x")
    done = r.resume(2, approve(comment="ok"))
    assert done["state"] == "completed"
    assert done["values"]["approval_status"] == "approved"
    assert done["values"]["decided_by"] == 7
    assert done["values"]["result"] == {"outcome": "approved", "written": True}
    assert len(w.calls) == 1 and w.calls[0]["outcome"] == "approved"


def test_rejected_resume_writes_nothing():
    w = RecordingWriter()
    r = make(w)
    r.start(3, "x")
    done = r.resume(3, {"decision": "rejected", "comment": "out of scope", "decided_by": 7})
    assert done["values"]["result"] == {"outcome": "rejected", "written": False}
    assert w.calls == []


def test_modified_resume_keeps_pm_comment():
    w = RecordingWriter()
    r = make(w)
    r.start(4, "x")
    done = r.resume(4, {"decision": "modified", "comment": "split into two stories", "decided_by": 7})
    assert done["values"]["approval_status"] == "modified"
    assert done["values"]["pm_comment"] == "split into two stories"
    assert w.calls[0]["outcome"] == "modified"


BAD = [
    {"decision": "maybe", "comment": "x", "decided_by": 7},
    {"decision": "approved", "comment": "", "decided_by": 0},
    {"decision": "approved", "comment": "", "decided_by": True},
    {"decision": "approved", "comment": ""},
    {"decision": "rejected", "comment": "", "decided_by": 7},
    {"decision": "modified", "comment": "   ", "decided_by": 7},
    {"decision": "approved", "comment": "", "decided_by": 7, "extra": "x"},
    None,
    "approved",
]


@pytest.mark.parametrize("bad", BAD)
def test_invalid_decisions_rejected_and_graph_keeps_waiting(bad):
    w = RecordingWriter()
    r = make(w)
    r.start(5, "x")
    with pytest.raises(InvalidDecision):
        r.resume(5, bad)
    assert r.status(5)["state"] == "waiting_for_pm"
    assert w.calls == []


def test_same_thread_resumes_same_state_from_a_new_runner():
    saver, w = memory_checkpointer(), RecordingWriter()
    first = GraphRunner(saver, w)
    run_id = first.start(6, "x")["run_id"]
    second = GraphRunner(saver, w)  # new compiled graph, same checkpoints
    assert second.status(6)["run_id"] == run_id
    assert second.resume(6, approve())["state"] == "completed"


def test_project_scope_is_preserved():
    w = RecordingWriter()
    r = make(w)
    a, b = r.start(11, "a"), r.start(12, "b")
    assert a["thread_id"] != b["thread_id"]
    assert a["values"]["project_id"] == 11 and b["values"]["project_id"] == 12
    r.resume(12, approve())
    assert r.status(11)["state"] == "waiting_for_pm"
    with pytest.raises(NotWaitingForDecision):
        r.resume(13, approve())
    assert [c["project_id"] for c in w.calls] == [12]


def test_duplicate_resume_does_not_duplicate_business_write():
    w = RecordingWriter()
    r = make(w)
    r.start(7, "x")
    r.resume(7, approve())
    with pytest.raises(NotWaitingForDecision):
        r.resume(7, approve())
    assert len(w.calls) == 1


def test_cannot_start_while_waiting():
    r = make()
    r.start(8, "x")
    with pytest.raises(AlreadyWaiting):
        r.start(8, "again")


def test_state_contains_only_allowed_keys():
    r = make()
    st = r.start(9, "x")
    assert set(st["values"]) <= ALLOWED_STATE_KEYS


def test_checkpoint_metadata_has_project_and_thread():
    saver = memory_checkpointer()
    GraphRunner(saver, RecordingWriter()).start(21, "x")
    tup = saver.get_tuple({"configurable": {"thread_id": "project:21"}})
    assert tup.metadata.get("project_id") == 21  # if this fails, metadata is not propagated by this langgraph version


def test_parse_decision_accepts_valid():
    assert parse_decision(approve()).decision == "approved"


def test_audit_business_writer_is_idempotent():
    w = AuditBusinessWriter()
    run_id = "run-" + str(random.randint(10**8, 10**9))
    kw = dict(project_id=1, thread_id="project:1", run_id=run_id, outcome="approved", payload={})
    assert w.write(**kw) is True
    assert w.write(**kw) is False
    with SessionLocal() as s:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.entity_id == run_id, AuditLog.action == "graph.finalized")))
    assert len(rows) == 1


def test_checkpoint_survives_restart_postgres():
    pid = random.randint(10**8, 10**9)
    thread = make_thread_id(pid)
    writer = AuditBusinessWriter()
    with postgres_checkpointer() as saver:
        st = GraphRunner(saver, writer).start(pid, "restart test")
        assert st["state"] == "waiting_for_pm"
        run_id = st["run_id"]
    # connection closed: this is the "restart"
    with postgres_checkpointer() as saver2:
        try:
            r2 = GraphRunner(saver2, writer)
            again = r2.status(pid)
            assert again["state"] == "waiting_for_pm" and again["run_id"] == run_id
            done = r2.resume(pid, approve())
            assert done["state"] == "completed" and done["values"]["result"]["written"] is True
            with pytest.raises(NotWaitingForDecision):
                r2.resume(pid, approve())
        finally:
            delete = getattr(saver2, "delete_thread", None)
            if delete:
                delete(thread)
    with SessionLocal() as s:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.entity_id == run_id, AuditLog.action == "graph.finalized")))
    assert len(rows) == 1
