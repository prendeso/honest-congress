from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from typing import Generator

from src.config import get_settings
from src.db.models import Base


settings = get_settings()


def _normalize_database_url(url: str) -> str:
    """Normalize Postgres URL variants to the SQLAlchemy + psycopg3 form.

    Accepts:
        postgres://...           (Railway / Heroku style)
        postgresql://...         (canonical libpq style)
        postgresql+psycopg://... (already correct)
    Returns a URL that SQLAlchemy will route through the psycopg3 driver.
    Non-Postgres URLs (e.g. sqlite://) are returned unchanged.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


db_url = _normalize_database_url(settings.database_url)

# Create engine with appropriate settings for SQLite vs PostgreSQL
if db_url.startswith("sqlite"):
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        echo=settings.log_level == "DEBUG"
    )
else:
    engine = create_engine(
        db_url,
        echo=settings.log_level == "DEBUG",
        pool_pre_ping=True
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)


def drop_db() -> None:
    """Drop all database tables."""
    Base.metadata.drop_all(bind=engine)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Get a database session context manager."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

