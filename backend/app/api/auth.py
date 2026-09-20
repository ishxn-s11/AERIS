"""Authentication endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user, require_roles
from app.database.session import get_db
from app.models.models import User, UserRole
from app.schemas.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services import user_service
from app.utils.security import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = user_service.authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    token = create_access_token(subject=user.email, role=user.role.value)
    return TokenResponse(access_token=token, role=user.role.value, name=user.name)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
    actor: User | None = Depends(get_optional_user),
) -> User:
    """Open registration only until the first user exists; afterwards admin-only.

    Keeps first-run setup friction-free while preventing privilege escalation on
    a running deployment.
    """
    if user_service.count_users(db) > 0:
        if actor is None or actor.role != UserRole.ADMIN:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only admins can create accounts after initial setup",
            )
    try:
        user = user_service.create_user(db, body.name, body.email, body.password, body.role)
    except user_service.UserError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return user


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN)),
):
    return db.scalars(select(User).order_by(User.id)).all()
