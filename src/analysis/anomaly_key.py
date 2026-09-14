"""What makes two anomaly findings the same finding.

Three writers persisted anomalies with three different answers to that
question -- `persist_anomalies`, `WealthAnalyzer` and `TradeAnalyzer` -- and a
single unique index enforced one of them, `(member_id, anomaly_type, title)`.
That index encoded `persist_anomalies`' key as if it were universal. It is not,
and the first production analysis run with real data died on it:

    UniqueViolation: duplicate key value violates unique constraint
    "uq_anomaly_member_type_title"
    DETAIL:  Key (member_id, anomaly_type, title)=(12188, late_filing,
    Late PTR filing: severely late (over 3 months)) already exists.

Nothing was wrong with either row. A `late_filing` title is a *bucket* --
"severely late (over 3 months)" -- so a member who filed two trades that late
produces two findings with the same title and different `transaction_id`s. The
index made that unrepresentable, which would have meant reporting one late
trade per member per bucket and silently dropping the rest.

So there are two identities, not one, and which applies depends on what the
finding is about:

* about a specific trade -> the trade identifies it: (member, type, transaction)
* about the member overall -> the title identifies it: (member, type, title)

`identity_of` returns whichever applies, and the schema enforces exactly these
two with a pair of partial unique indexes. Every writer now asks this module
instead of carrying its own answer.
"""

from typing import TYPE_CHECKING, Any, Dict, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

# A title longer than the column is truncated on write, so the key has to be
# built from the truncated value or the in-batch check and the database
# disagree about what a duplicate is.
TITLE_LIMIT = 200

Identity = Tuple[Any, ...]


def identity_of(anomaly: Dict[str, Any], title: str | None = None) -> Identity | None:
    """The key that decides whether this finding is already recorded.

    `title` overrides `anomaly["title"]` for callers that build the title
    themselves. Returns None when the finding cannot be keyed at all -- no
    member or no type -- which is the existing "skip it" case.
    """
    member_id = anomaly.get("member_id")
    anomaly_type = anomaly.get("anomaly_type")
    if not member_id or not anomaly_type:
        return None

    transaction_id = anomaly.get("transaction_id")
    if transaction_id is not None:
        return (member_id, anomaly_type, "transaction", transaction_id)

    effective_title = title if title is not None else (anomaly.get("title") or "")
    return (member_id, anomaly_type, "title", effective_title[:TITLE_LIMIT])


def find_existing(db: "Session", identity: Identity) -> Any:
    """The already-stored finding with this identity, or None.

    The filter mirrors the partial indexes exactly, `transaction_id IS NULL`
    included. Without that clause the title branch would also match
    trade-level rows, and a member-level finding would be suppressed by an
    unrelated finding about one of their trades that happened to share a title.
    """
    from src.db.models import Anomaly

    _member_id, _anomaly_type, kind, value = identity
    query = db.query(Anomaly).filter(
        Anomaly.member_id == _member_id,
        Anomaly.anomaly_type == _anomaly_type,
    )
    if kind == "transaction":
        query = query.filter(Anomaly.transaction_id == value)
    else:
        query = query.filter(Anomaly.transaction_id.is_(None), Anomaly.title == value)
    return query.first()


def identity_of_row(row: Any) -> Identity:
    """The same key, derived from a stored row rather than a candidate dict.

    The two branches mirror the two partial unique indexes exactly: a row that
    carries a `transaction_id` is keyed by the trade, and a row without one is
    keyed by its title. That is what makes a dict built from this equivalent to
    `find_existing`'s per-row query, including the `transaction_id IS NULL`
    clause -- a title key can only ever match a row that has no trade.
    """
    if row.transaction_id is not None:
        return (row.member_id, row.anomaly_type, "transaction", row.transaction_id)
    return (row.member_id, row.anomaly_type, "title", (row.title or "")[:TITLE_LIMIT])


def stored_by_identity(db: "Session") -> Dict[Identity, Any]:
    """Every stored finding, keyed by identity, in one query rather than one each.

    `find_existing` is a round trip per candidate finding, and it is called from
    three places: both analyzers' roster walks and `persist_anomalies`. Against a
    hosted database that is one network round trip per finding the suite
    proposes, on every run, almost all of them answering "yes, already there".

    The rows are returned rather than just the keys because `TradeAnalyzer` uses
    the existing row: a trade-level finding is allowed to be restated, and it
    updates in place instead of duplicating.

    Two stored rows cannot collide here -- the partial unique indexes are exactly
    these two identities -- so building a dict cannot silently drop one.

    Callers still need their own in-batch `seen` set: rows added but not
    committed are not in here, and `SessionLocal` is autoflush=False so a query
    would not see them either.
    """
    from src.db.models import Anomaly

    return {identity_of_row(row): row for row in db.query(Anomaly).all()}
