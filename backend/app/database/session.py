"""SQLAlchemy engine, session factory and declarative base for AERIS."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _make_engine():
    url = settings.sqlalchemy_database_uri
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


engine = _make_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> "Session":
    """FastAPI dependency yielding a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
