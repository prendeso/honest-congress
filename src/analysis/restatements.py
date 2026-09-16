"""An amended filing restates its original, and every detector counted it twice.

A member who amends a disclosure does not file the difference. They file the
whole thing again. Confirmed live:

    disc 2426  'Annual Report for CY 2024'               Thom Tillis  32 txns
    disc 2859  'Annual Report for CY 2024 (Amendment 1)' Thom Tillis  33 txns
    identical (date, description, amount) rows: 32 of 32

Both rows sets are stored. Nothing in the schema links an amendment to what it
amends -- `Disclosure` has no `amends_id`, `is_amendment` or version column --
so every detector that reads a member's transactions reads each restated trade
once per filing that carries it. The published consequences, measured:

    Thom Tillis     "14 consecutive trades"   7 real trades, each stored twice
    Steve Daines    "26 consecutive trades"   13 real trades
    Dave McCormick  "105 consecutive trades"  212 of his 603 rows are restatements

and the effect is not uniformly a doubling. `_check_volume_spikes` gates on
`len(amounts) >= 8`, so four real trades open it. `_check_perfect_timing` runs a
nested buy x sell loop, so duplication is quadratic. `significance` builds both
its observed streams and its permutation nulls from these rows, so the q-values
move in a direction nobody can predict from the inflation factor.

`late_filing` is the one that can do more than overcount. It scores a trade
against the filing that reports it, so a restatement filed later re-scores an
already-timely trade as late. `catalog.py` states that caveat in prose -- "an
amended filing can make a timely report look late" -- and nothing acted on it.

Stated precisely, because it is an accusation about a named person: PTR
restatements are confirmed to exist (34 identical trades across two PTRs in a
ten-member sample), but every live pair found so far was filed on the SAME day,
which shifts no deadline. So the timely->late flip is what keeping the earliest
copy PREVENTS, not damage this module is known to be undoing. The double count
is measured; the false accusation is a reachable consequence of the same defect.

**The rule is content-based, deliberately.** Identifying amendments structurally
is not available: House filing types are single letters whose meanings are
undocumented here (`D`, `W`, `B`, `E` all occur and none is known to mean
"amendment"), and the Senate stores a free-text label -- "Annual Report for CY
2024 (Amendment 1)" -- that `senate.py` preserves verbatim and never parses. So
a restatement is recognised by what a filing says, not by what it is called:

    For one member, a LATER filing restates an EARLIER one when its type is
    labelled an amendment, or when the two share at least three rows identical
    on (transaction_date, transaction_type, description, ticker, amount_min,
    amount_max, owner). The shared rows are then dropped from the later filing.

The decision is deliberately made between FILINGS rather than between rows.
A single identical row shared by two filings is a coincidence a real pair of
trades could produce; thirty-two of them is a document being refiled. Judging
row by row would have deleted disclosed trades -- it did, in the compliance
tests, where four separate PTRs each report one same-day purchase of the same
stock in the same band.

Three properties of that rule carry weight and all are tested:

* **Never within one filing.** Two identical rows in a single document are two
  real trades -- a member can buy the same stock twice in a day in the same
  band. Collapsing those would delete disclosed trades, which is a worse error
  than the one this module fixes.
* **Earliest copy wins.** That is what repairs `late_filing`: the trade is
  scored against the filing that FIRST reported it, which is the filing the
  STOCK Act deadline actually runs against.

* **Filing-level evidence, not row-level.** Below the threshold nothing is
  dropped, so a chance collision cannot delete a trade.

The residual risk is a member making three or more identical trades on the same
day, split across two filings, which this would wrongly collapse. Every compared
field must match, owner included, and a trade is reported once. Against that:
leaving it unfixed publishes inflated counts about named people today.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Mapping, Sequence

from sqlalchemy.orm import Session

from src.db.models import Disclosure, Transaction

# The Senate writes the label; the House never does. When it is there it is
# decisive, which is why a labelled amendment needs no corroboration.
_AMENDMENT_LABEL = re.compile(r"amend", re.IGNORECASE)

# Filing-level evidence standing in for the label the House does not provide.
# One identical row shared by two filings is a coincidence two members could
# produce; three is a filing being restated. Set low because the real cases are
# nowhere near it -- Tillis shares 32 rows, McCormick 190.
MIN_SHARED_ROWS_FOR_RESTATEMENT = 3


def content_key(transaction: Transaction) -> tuple:
    """What makes two stored rows the same disclosed trade.

    Amount bounds are compared as strings because they are `Numeric` and come
    back as `Decimal`: `Decimal("1001")` and `Decimal("1001.00")` are equal in
    Python but a restated row can carry either, so `str()` on the raw value
    would split them. `format(v, "f")` normalises the scale.
    """

    def amount(value) -> str:
        return format(value, "f") if value is not None else ""

    return (
        transaction.transaction_date,
        getattr(transaction.transaction_type, "value", transaction.transaction_type),
        (transaction.description or "").strip(),
        (transaction.ticker or "").strip().upper(),
        amount(transaction.amount_min),
        amount(transaction.amount_max),
        (transaction.owner or "").strip(),
    )


def drop_restatements(
    transactions: Sequence[Transaction],
    filed_on: Mapping[int, object],
    # `str | None` because a narrow projection may not select the column at
    # all; a filing whose type is unknown simply falls back to the shared-row
    # test, which is the conservative branch.
    types: Mapping[int, str | None] | None = None,
) -> List[Transaction]:
    """One row per disclosed trade, keeping the earliest filing that reported it.

    The decision is made between FILINGS, not between rows, and that distinction
    is the whole guard against deleting real trades. One identical row shared by
    two filings is weak evidence of a restatement -- two members' worth of
    coincidence would look the same. Thirty-two identical rows is not evidence,
    it is proof.

    So a later filing is treated as restating an earlier one when either:

    * its type says so -- the Senate writes "Annual Report for CY 2024
      (Amendment 1)" and `senate.py` preserves that label verbatim, so when it
      is there it is decisive; or
    * the two share at least `MIN_SHARED_ROWS_FOR_RESTATEMENT` identical rows,
      which is the filing-level evidence that stands in for a label the House
      never provides.

    Only then are the shared rows dropped from the later filing. Everything else
    survives, including rows repeated INSIDE one filing: a member can buy the
    same stock twice in a day in the same band, and collapsing those would
    delete a disclosed trade -- a worse error than the overcount being fixed.

    `filed_on` maps disclosure id -> filing date; `types` maps disclosure id ->
    filing type and may be omitted, in which case only the shared-row test
    applies.
    """
    if not transactions:
        return []

    types = types or {}
    by_disclosure: Dict[int, List[Transaction]] = {}
    for transaction in transactions:
        by_disclosure.setdefault(transaction.disclosure_id, []).append(transaction)

    def filed(disclosure_id: int) -> tuple:
        when = filed_on.get(disclosure_id)
        # Unknown date sorts LAST, so a filing whose date we hold always wins
        # over one we do not, rather than the answer depending on row order.
        return (when is None, when, disclosure_id)

    order = sorted(by_disclosure, key=filed)
    counts = {
        disclosure_id: Counter(content_key(t) for t in rows)
        for disclosure_id, rows in by_disclosure.items()
    }

    dropped: set[int] = set()
    for position, later in enumerate(order):
        labelled = _AMENDMENT_LABEL.search(types.get(later) or "") is not None
        # How many copies of each key earlier filings already accounted for.
        already: Counter = Counter()
        for earlier in order[:position]:
            shared = counts[later] & counts[earlier]
            if not shared:
                continue
            if not labelled and sum(shared.values()) < MIN_SHARED_ROWS_FOR_RESTATEMENT:
                continue
            already |= shared

        if not already:
            continue
        # Drop only as many copies as the earlier filings carried. If the later
        # filing reports the same trade twice where the earlier reported it
        # once, the second copy is a trade the original omitted, not a repeat.
        seen: Counter = Counter()
        for transaction in by_disclosure[later]:
            key = content_key(transaction)
            if seen[key] < already.get(key, 0):
                seen[key] += 1
                dropped.add(id(transaction))

    kept = [t for t in transactions if id(t) not in dropped]
    kept.sort(key=lambda t: (t.transaction_date is None, t.transaction_date, t.id or 0))
    return kept


def filing_dates(db: Session, disclosure_ids: Iterable[int]) -> Dict[int, object]:
    ids = [i for i in set(disclosure_ids) if i is not None]
    if not ids:
        return {}
    rows = db.query(Disclosure.id, Disclosure.filing_date).filter(Disclosure.id.in_(ids)).all()
    return {row[0]: row[1] for row in rows}


def without_restatements(db: Session, transactions: Sequence[Transaction]) -> List[Transaction]:
    """Convenience for callers that already hold the rows."""
    ids = [t.disclosure_id for t in transactions]
    return drop_restatements(transactions, filing_dates(db, ids), filing_types(db, ids))


def filing_types(db: Session, disclosure_ids: Iterable[int]) -> Dict[int, str | None]:
    ids = [i for i in set(disclosure_ids) if i is not None]
    if not ids:
        return {}
    rows = db.query(Disclosure.id, Disclosure.filing_type).filter(Disclosure.id.in_(ids)).all()
    return {row[0]: row[1] for row in rows}


def drop_restated_pairs(rows: Sequence[tuple]) -> List[tuple]:
    """The same rule for callers that select `(Transaction, Disclosure)` pairs.

    Several detectors need the filing alongside the trade -- `late_filing`
    compares the two dates -- so they select both. The filing date is then
    already in hand and needs no second query.

    Rows are grouped by the disclosure's OWN member, so two members with
    identical trades are never merged.
    """
    filed: Dict[int, object] = {}
    kinds: Dict[int, str] = {}
    by_member: Dict[object, List[Transaction]] = {}
    paired: Dict[int, tuple] = {}

    for row in rows:
        transaction, disclosure = row[0], row[1]
        filed[disclosure.id] = disclosure.filing_date
        kinds[disclosure.id] = disclosure.filing_type
        by_member.setdefault(disclosure.member_id, []).append(transaction)
        paired[transaction.id] = row

    kept: List[tuple] = []
    for transactions in by_member.values():
        for transaction in drop_restatements(transactions, filed, kinds):
            kept.append(paired[transaction.id])
    return kept


def drop_restated_records(rows: Sequence) -> List:
    """The same rule for callers that select labelled columns, not entities.

    `tier2_detectors` and `significance` build narrow projections for speed
    rather than loading `Transaction` objects. Their rows only need to expose
    the fields `content_key` reads, plus `id`, `disclosure_id`, `member_id` and
    `filing_date` -- which is why those queries now select them.

    `significance` is the reason this exists rather than being skipped as a
    micro-optimisation: it builds BOTH the observed event streams and the
    permutation nulls from these rows, so inflated input does not make the
    q-values conservative, it makes them unpredictable. The q-value is the
    number this project tells readers to trust above the raw counts.
    """
    if not rows:
        return []

    filed = {row.disclosure_id: getattr(row, "filing_date", None) for row in rows}
    kinds = {row.disclosure_id: getattr(row, "filing_type", None) for row in rows}
    by_member: Dict[object, List] = {}
    for row in rows:
        by_member.setdefault(row.member_id, []).append(row)

    kept: List = []
    for records in by_member.values():
        kept.extend(drop_restatements(records, filed, kinds))
    return kept


def member_transactions(
    db: Session,
    member_id: int,
    *,
    ptr_only: bool = False,
    parsed_only: bool = False,
) -> List[Transaction]:
    """Every trade a member disclosed, once each, oldest first.

    The one place detectors should read a member's trades from. Every call site
    it replaced wrote the same join by hand and none of them de-duplicated.
    """
    query = (
        db.query(Transaction)
        .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
        .filter(Disclosure.member_id == member_id)
    )
    if ptr_only:
        query = query.filter(Disclosure.is_ptr.is_(True))
    if parsed_only:
        query = query.filter(Disclosure.parsed.is_(True))

    rows = query.all()
    return drop_restatements(
        rows,
        _dates_for(db, member_id),
        filing_types(db, [t.disclosure_id for t in rows]),
    )


def transactions_by_member(
    db: Session,
    member_ids: Iterable[int] | None = None,
) -> Dict[int, List[Transaction]]:
    """The same thing for many members in one query, for the sweep detectors."""
    query = db.query(
        Transaction, Disclosure.member_id, Disclosure.filing_date, Disclosure.filing_type
    ).join(Disclosure, Transaction.disclosure_id == Disclosure.id)
    kinds: Dict[int, str] = {}
    ids = None if member_ids is None else [i for i in set(member_ids) if i is not None]
    if ids is not None:
        if not ids:
            return {}
        query = query.filter(Disclosure.member_id.in_(ids))

    grouped: Dict[int, List[Transaction]] = {}
    filed: Dict[int, object] = {}
    for transaction, member_id, filing_date, filing_type in query.all():
        grouped.setdefault(member_id, []).append(transaction)
        filed[transaction.disclosure_id] = filing_date
        kinds[transaction.disclosure_id] = filing_type

    return {member_id: drop_restatements(rows, filed, kinds) for member_id, rows in grouped.items()}


def _dates_for(db: Session, member_id: int) -> Dict[int, object]:
    rows = (
        db.query(Disclosure.id, Disclosure.filing_date)
        .filter(Disclosure.member_id == member_id)
        .all()
    )
    return {row[0]: row[1] for row in rows}
