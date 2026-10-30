"""Typed state for the orchestration graph. Execution state only, never business truth."""
from typing import Literal, TypedDict

GRAPH_VERSION = "spike-1"
ApprovalStatus = Literal["pending", "approved", "rejected", "modified"]


class GraphState(TypedDict, total=False):
    project_id: int
    thread_id: str
    run_id: str
    input_text: str
    draft: str | None
    approval_status: ApprovalStatus
    pm_comment: str | None
    decided_by: int | None
    errors: list[str]
    graph_version: str
    result: dict | None


ALLOWED_STATE_KEYS = frozenset(GraphState.__annotations__)


def make_thread_id(project_id: int) -> str:
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise ValueError("project_id must be a positive integer")
    return f"project:{project_id}"
