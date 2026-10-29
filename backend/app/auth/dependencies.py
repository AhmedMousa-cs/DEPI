from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy.orm import Session
from app.config import settings
from app.db.models import User, ProjectMember
from app.db.session import get_db

security_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid authentication token"
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid authentication token"
        )

    user = db.get(User, int(user_id))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Permission denied")
        return user

    return dependency


def require_project_access(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if user.role == "PM":
        return user
    link = db.get(ProjectMember, (project_id, user.id))
    if link is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access to project denied")
    return user
