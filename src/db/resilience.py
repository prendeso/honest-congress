"""Surviving a database connection that dies in the middle of a long run.

On 2026-09-14 the nightly cron spent 3h54m in the bill sponsorship ingest and
then died:

    psycopg.OperationalError: consuming input failed:
        SSL error: unexpected eof while reading

Railway's proxy dropped the connection. `pool_pre_ping=True` is set and does not
cover it: pre-ping validates a connection at checkout, and this one died partway
through a transaction that had been checked out seconds earlier. Over a step
measured in hours that is a matter of probability rather than configuration.

Two things make this worth a shared module rather than four copies.

The first is deciding what to retry. SQLAlchemy already knows: `DBAPIError`
carries `connection_invalidated`, set when the dialect's `is_disconnect` fires,
which for psycopg means the connection is closed or broken. Matching on the
message text instead would be guesswork, and would eventually retry a constraint
violation -- turning a loud bug into a silent one.

The second is what a rollback does to the caller's caches, which is the part
that is easy to get wrong and dangerous to get wrong. Every one of these
ingesters preloads an existence cache -- a set of external ids, a dict of
natural key to row id -- and adds to it as the loop goes. A rollback undoes rows
the cache says exist. Afterwards the cache is lying: it will skip re-importing a
row that is no longer there, or hand out an id no row has any more. So the
rebuild is not tidying up after the failure; it is the failure handling.

`commit_or_recover` therefore takes the rebuild as an argument rather than
hiding it. Every call site has to name what it rebuilds, in the open, where the
next person reading the loop can check it against what the loop caches.
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# How many times one unit of work is retried after the connection drops. One is
# the useful number: these are momentary, and a connection that fails twice in a
# row is not going to be talked round by a third attempt.
CONNECTION_LOSS_RETRIES = 1


def is_a_dropped_connection(exc: SQLAlchemyError) -> bool:
    """Whether the connection died, as opposed to the database refusing the work.

    SQLAlchemy decides, not a message match. A constraint violation, a bad query
    or a deadlock leave the connection perfectly healthy and must keep raising.
    """
    return bool(getattr(exc, "connection_invalidated", False))


def commit_or_recover(
    db: Session,
    *,
    rebuild_caches: Callable[[], None],
    unit: str,
) -> bool:
    """Commit the pending work. True if it landed, False if the connection died.

    On a dropped connection this rolls back, rebuilds the caller's caches from
    what is actually committed, and returns False. It does not decide what
    happens next: the caller knows whether the work can be replayed from memory,
    whether replaying it costs another API request, and what to record if it
    cannot. Anything that is not a dropped connection is re-raised untouched.
    """
    try:
        db.commit()
        return True
    except SQLAlchemyError as exc:
        if not is_a_dropped_connection(exc):
            raise
        logger.warning(
            "Database connection dropped while storing %s (%s); rolling back and rebuilding caches",
            unit,
            exc.__class__.__name__,
        )
        db.rollback()
        # Mandatory. The rollback has just undone rows the caller's caches say
        # exist; until this runs they are wrong in the direction that skips
        # re-importing them, or hands out an id no row has.
        rebuild_caches()
        return False
