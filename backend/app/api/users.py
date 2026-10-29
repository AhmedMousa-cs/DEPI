from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.db.models import User
from app.db.session import get_db
from app.schemas.team import UserBrief

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/team-leaders", response_model=list[UserBrief])
def list_team_leaders(
    db: Session = Depends(get_db), user: User = Depends(require_role("PM"))
):
    return list(
        db.scalars(select(User).where(User.role == "TEAM_LEADER").order_by(User.name))
    )
