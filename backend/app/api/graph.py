from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.common import get_project_or_404
from app.auth.dependencies import require_project_access, require_role
from app.db.models import User
from app.db.session import get_db
from app.orchestrator.audit_hook import db_graph_audit
from app.orchestrator.checkpoints import postgres_checkpointer
from app.orchestrator.decisions import InvalidDecision
from app.orchestrator.graph import (
    MAX_INPUT_CHARS,
    AlreadyWaiting,
    AuditBusinessWriter,
    GraphRunner,
    NotWaitingForDecision,
)

router = APIRouter(prefix="/projects/{project_id}/graph", tags=["graph"])


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_text: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)


class ResumeBody(BaseModel):
    """decided_by is NOT accepted here: it always comes from the authenticated PM."""
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected", "modified"]
    comment: str = Field(default="", max_length=2000)


def _runner(saver) -> GraphRunner:
    return GraphRunner(saver, AuditBusinessWriter(), audit=db_graph_audit)


@router.post("/start")
def start_graph(project_id: int, body: StartBody, db: Session = Depends(get_db),
                user: User = Depends(require_role("PM"))):
    get_project_or_404(db, project_id)
    with postgres_checkpointer() as saver:
        try:
            return _runner(saver).start(project_id, body.input_text)
        except AlreadyWaiting as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))


@router.post("/resume")
def resume_graph(project_id: int, body: ResumeBody, db: Session = Depends(get_db),
                 user: User = Depends(require_role("PM"))):
    get_project_or_404(db, project_id)
    raw = {"decision": body.decision, "comment": body.comment, "decided_by": user.id}
    with postgres_checkpointer() as saver:
        try:
            return _runner(saver).resume(project_id, raw)
        except NotWaitingForDecision as exc:
            raise HTTPException(409, str(exc))
        except InvalidDecision as exc:
            raise HTTPException(422, str(exc))


@router.get("/status")
def graph_status(project_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require_project_access)):
    get_project_or_404(db, project_id)
    with postgres_checkpointer() as saver:
        return _runner(saver).status(project_id)
