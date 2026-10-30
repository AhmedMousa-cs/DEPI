import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.db.models import ProjectMember, User
from app.db.session import get_db

# auto_error=False so a missing token is always 401 (not 403, which depends on the FastAPI version)
security_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, TypeError, ValueError):
        raise _unauthorized("Invalid authentication token")
    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized("User not found")
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
    if db.get(ProjectMember, (project_id, user.id)) is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access to project denied")
    return user
