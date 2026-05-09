"""Test configuration and fixtures.

We point DATABASE_URL at a fresh temporary SQLite file BEFORE any test
module imports `src.config` / `src.db.database`. That binding happens at
import time and is sticky — so this has to run first. pytest evaluates
conftest.py before any test file, which is why the side effect lives here.
"""

import os
import tempfile

import pytest

# Tempfile (not :memory:) so the FastAPI handlers and the seed fixture
# share the same DB across SQLAlchemy connection-pool checkouts.
_TMP_FD, _TMP_PATH = tempfile.mkstemp(suffix=".db", prefix="honest_pytest_")
os.close(_TMP_FD)
os.environ.pop("ENV", None)
os.environ.pop("ADMIN_PASSWORD", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_PATH}"

# Now safe to import — the engine will bind to the temp DB above.
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.db.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_test_database():
    """Create the schema once at session start and clean up after."""
    from src.db.database import engine as app_engine

    Base.metadata.drop_all(bind=app_engine)
    Base.metadata.create_all(bind=app_engine)
    yield
    Base.metadata.drop_all(bind=app_engine)
    if os.path.exists(_TMP_PATH):
        os.unlink(_TMP_PATH)


@pytest.fixture(scope="session")
def engine():
    """Legacy in-memory engine for tests that don't need the FastAPI app."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture(scope="function")
def db_session(engine):
    """In-memory session for unit tests of analyzers / models / parsers."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    Base.metadata.create_all(bind=engine)

    yield session

    session.rollback()
    session.close()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
