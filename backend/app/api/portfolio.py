"""M7 PM command center API: portfolio overview, resources, risks + the PM assistant chat.

All endpoints accept the PM and any team leader (leaders see only their own projects —
the scoping lives in services.portfolio._accessible_projects). The four read endpoints
are deterministic (no LLM); /chat is the only LLM call and it goes through run_structured.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_role
from app.db.models import User
from app.db.session import get_db
from app.schemas.portfolio import PortfolioChatInput
from app.services import portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/overview")
def get_overview(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return portfolio.portfolio_overview(db, user)


@router.get("/resources")
def get_resources(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return portfolio.portfolio_resources(db, user)


@router.get("/risks")
def get_risks(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return portfolio.portfolio_risks(db, user)


@router.post("/chat")
def post_chat(
    body: PortfolioChatInput,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("PM")),
):
    if not any(m.role == "user" for m in body.messages):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "chat needs at least one user message")
    result = portfolio.portfolio_chat(db, user, [m.model_dump() for m in body.messages])
    db.commit()
    return result
