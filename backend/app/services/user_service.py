"""User account logic kept out of API routes (thin controllers)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import User, UserRole
from app.utils.security import hash_password, verify_password


class UserError(Exception):
    """Domain-level user error (duplicate email, bad role, etc.)."""


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.lower()))


def count_users(db: Session) -> int:
    return db.scalar(select(func.count(User.id))) or 0


def create_user(db: Session, name: str, email: str, password: str, role: str) -> User:
    try:
        user_role = UserRole(role)
    except ValueError as exc:
        raise UserError(f"Invalid role '{role}'") from exc
    if get_user_by_email(db, email) is not None:
        raise UserError(f"Email already registered: {email}")
    user = User(name=name, email=email, password_hash=hash_password(password), role=user_role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
