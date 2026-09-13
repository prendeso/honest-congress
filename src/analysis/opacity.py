"""How legibly a member discloses.

Every other measure here asks what a member did. This one asks how clearly they
said it -- what share of their filings are missing a ticker, describe a holding
as "various", report an amount that cannot be read, or could not be read at all.

That last component is the one that took two attempts. It used to be `not
parsed`, which missed the worst case completely: a scan of a paper form IS
parsed -- the parser ran, raised nothing and extracted nothing -- so a member
filing exclusively on paper scored 0% unparsed, contributed no items to any
denominator, and came out perfectly legible. If none of their filings were
readable they fell below MIN_ITEMS_FOR_SCORE and left the leaderboard
altogether. 12.7% of 2024-25 House trade reports are such scans.

A member whose filings cannot be parsed is not thereby suspicious, and this
makes no such claim: the House and Senate accept free-text filings, handwriting
and scanned PDFs, so opacity is often the filing system's fault rather than the
filer's. But it is measurable, nobody publishes it, and it bounds what every
other detector here can see. A member with 80% unreadable filings will look
clean under every trade detector for reasons that have nothing to do with their
conduct -- which is worth stating out loud rather than letting a reader assume
absence of findings means absence of activity.

That makes it the most on-brand thing the project computes: honest about the
limits of its own inputs, which is the sense in which "honest" was meant.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.db.models import Asset, Disclosure, Member, Transaction

logger = logging.getLogger(__name__)

# Descriptions that identify nothing. Matched on word boundaries against the
# whole description, not as substrings.
VAGUE_PATTERNS = re.compile(
    r"\b(various|misc|miscellaneous|other|unknown|see\s+attached|n/?a|none|"
    r"undetermined|not\s+applicable|redacted)\b",
    re.IGNORECASE,
)

# Below this many filings the percentages are not worth reporting.
MIN_ITEMS_FOR_SCORE = 5


def _is_vague(description: str | None) -> bool:
    if not description or not description.strip():
        return True
    text = description.strip()
    if len(text) < 3:
        return True
    # A description that is *only* a vague token carries nothing; one that
    # merely contains the word ("Various Industries Inc") may be a real name.
    stripped = VAGUE_PATTERNS.sub("", text).strip(" .,-—/")
    return not stripped


def member_opacity(db: Session, member: Member) -> Dict[str, Any] | None:
    """Legibility of one member's filings, or None if there is nothing to score."""
    disclosures = db.query(Disclosure).filter(Disclosure.member_id == member.id).all()
    if not disclosures:
        return None

    disclosure_ids = [d.id for d in disclosures]
    unparsed = sum(1 for d in disclosures if not d.parsed)
    parse_errors = sum(1 for d in disclosures if d.parse_error)
    scanned = sum(1 for d in disclosures if d.has_text_layer is False)
    # Every filing that produced nothing usable, however it failed to: never
    # parsed, a scan with no text in it, or parsed and scored zero.
    #
    # `not parsed` alone was the old measure and it missed the worst case
    # entirely. A scan of a paper form IS parsed -- the parser ran, raised
    # nothing, and extracted nothing -- so a member who files exclusively on
    # paper scored 0% unparsed and contributed no items at all, which put their
    # illegibility in neither the numerator nor the denominator. If every one
    # of their filings was a scan they fell below MIN_ITEMS_FOR_SCORE and
    # dropped off the board completely: the least legible filer in Congress,
    # excluded from the legibility ranking for being too illegible.
    unreadable_filings = sum(
        1
        for d in disclosures
        if not d.parsed or d.has_text_layer is False or d.parse_confidence == 0.0
    )

    transactions: List[Transaction] = (
        db.query(Transaction).filter(Transaction.disclosure_id.in_(disclosure_ids)).all()
    )
    assets: List[Asset] = db.query(Asset).filter(Asset.disclosure_id.in_(disclosure_ids)).all()

    items = len(transactions) + len(assets)
    if items < MIN_ITEMS_FOR_SCORE and not unreadable_filings:
        # Too little to say anything about -- unless the reason there is too
        # little is itself the finding.
        return None

    missing_ticker = sum(1 for t in transactions if not t.ticker)
    vague_description = sum(1 for t in transactions if _is_vague(t.description)) + sum(
        1 for a in assets if _is_vague(a.description)
    )
    unreadable_amount = sum(
        1 for t in transactions if t.amount_min is None and t.amount_max is None
    ) + sum(1 for a in assets if a.value_min is None and a.value_max is None)

    # Equal weight across the components, each a share of the things it can
    # apply to, so they stay comparable between members filing very different
    # volumes.
    ticker_rate = missing_ticker / len(transactions) * 100 if transactions else 0.0
    vague_rate = vague_description / items * 100 if items else 0.0
    amount_rate = unreadable_amount / items * 100 if items else 0.0
    unreadable_filing_rate = unreadable_filings / len(disclosures) * 100

    # Average only over the components that are DEFINED. A member with no
    # readable items has no ticker rate, no vagueness rate and no amount rate
    # -- those are undefined, not zero -- and averaging three zeroes in would
    # score the most opaque filer at 25 out of 100 and rank them as one of the
    # clearest. The filing-level component is always defined, because the
    # member has filings or this function returned above.
    defined = [unreadable_filing_rate]
    if transactions:
        defined.append(ticker_rate)
    if items:
        defined.extend((vague_rate, amount_rate))

    score = round(sum(defined) / len(defined), 2)

    return {
        "member_id": member.id,
        "member_name": f"{member.first_name} {member.last_name}",
        "bioguide_id": member.bioguide_id,
        "party": member.party.value if member.party else None,
        "state": member.state,
        "chamber": member.chamber.value if member.chamber else None,
        "opacity_score": score,
        "disclosures": len(disclosures),
        "items_scored": items,
        "components": {
            "transactions_missing_ticker": missing_ticker,
            "transactions_missing_ticker_percent": round(ticker_rate, 2),
            "items_vaguely_described": vague_description,
            "items_vaguely_described_percent": round(vague_rate, 2),
            "items_with_unreadable_amount": unreadable_amount,
            "items_with_unreadable_amount_percent": round(amount_rate, 2),
            "filings_unreadable": unreadable_filings,
            "filings_unreadable_percent": round(unreadable_filing_rate, 2),
            # The breakdown, because the three have different remedies. A scan
            # needs OCR nobody here does; an unparsed filing needs a re-run.
            "disclosures_unparsed": unparsed,
            "disclosures_scanned": scanned,
            "disclosures_with_parse_errors": parse_errors,
        },
    }


def opacity_leaderboard(db: Session, limit: int | None = None) -> Dict[str, Any]:
    """Members ranked from least to most legible."""
    # Only members who actually filed anything can be scored, and
    # `member_opacity` returns None for the rest -- so ask the disclosures
    # table who those are instead of walking the whole roster. This iterated
    # every Member and issued three queries inside each call: on the
    # production roster of 12,766 members that is ~38,000 round trips to
    # produce a list that can only ever contain filers.
    filer_ids = [row[0] for row in db.query(Disclosure.member_id).distinct().all()]

    scores = (
        [
            score
            for member in db.query(Member).filter(Member.id.in_(filer_ids)).all()
            if (score := member_opacity(db, member)) is not None
        ]
        if filer_ids
        else []
    )
    scores.sort(key=lambda s: -s["opacity_score"])

    return {
        "members_scored": len(scores),
        # Members below this are unscored -- unless the reason they are below
        # it is that their filings could not be read, which is the finding.
        "min_items": MIN_ITEMS_FOR_SCORE,
        "members": scores[:limit] if limit else scores,
        "note": (
            "Opacity measures how legible a member's filings are, not their conduct. "
            "House and Senate systems accept free text, scanned documents and "
            "handwriting -- 12.7% of 2024-25 House trade reports are photographs "
            "of paper forms with no machine-readable text at all -- so a high "
            "score often reflects the filing system rather than the filer. It is "
            "reported because it bounds what every other detector can see: a "
            "member whose filings cannot be read will show few findings for "
            "reasons unrelated to their trading."
        ),
    }
