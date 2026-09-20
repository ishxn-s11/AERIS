"""Shared API dependencies — authentication and role-based authorization."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt as pyjwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.models import User, UserRole
from app.utils.security import decode_access_token

# auto_error=False lets the registration endpoint distinguish "anonymous
# first-run setup" from "authenticated request".
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(token)
    except pyjwt.PyJWTError:
        raise _unauthorized() from None
    email = payload.get("sub")
    if not email:
        raise _unauthorized()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise _unauthorized()
    return user


def get_optional_user(
    token: str | None = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> User | None:
    """Returns the authenticated user, or None for anonymous requests."""
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except pyjwt.PyJWTError:
        return None
    email = payload.get("sub")
    if not email:
        return None
    return db.scalar(select(User).where(User.email == email))


def require_roles(*allowed: UserRole):
    """Dependency factory: restrict an endpoint to specific roles."""
    allowed_values = {r.value for r in allowed}

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role.value not in allowed_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role.value}' is not permitted to perform this action",
            )
        return user

    return checker
