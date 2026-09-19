"""Alembic migration environment.

Pulls the database URL from `src.config.get_settings()` so migrations and
the application share the same configuration. Honors Railway / Heroku
`postgres://` URLs via `_normalize_database_url`.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from src.config import get_settings
from src.db.database import _normalize_database_url
from src.db.models import Base

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False`, because the default is True and this
    # runs in-process whenever anything calls `cli init` or drives alembic
    # programmatically. With the default, applying a migration silently turns
    # OFF every logger configured before it -- `src.ingestion.orchestrator`
    # included -- so the next step in the same process runs mute.
    #
    # Caught by a test that migrates a SQLite database and an unrelated
    # orchestrator test that asserts on a warning it emits: the warning was
    # never recorded, and each test passed alone.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _get_url() -> str:
    return _normalize_database_url(get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
