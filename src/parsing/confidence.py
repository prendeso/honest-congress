"""How much of a filing the parser actually read.

Every trade in the system arrives through `ptr_parser.parse_ptr`, and
`orchestrator.parse_disclosure` marked a filing `parsed = True` on any run that
did not raise -- including one that extracted nothing at all. A filing read
cleanly and a filing barely read were indistinguishable in the database.

The score is a **completeness ratio**, not a weighted judgement::

    confidence = (rows_parsed / rows_detected) x (fields_extracted / fields_expected)

Both factors are counts of things that either happened or did not, which is the
point: this codebase has spent a long time removing asserted constants and
should not replace them with a fresh pile of invented weights. A dropped row
lowers the score by arithmetic. A transaction missing its amount lowers it by
arithmetic. Nobody has to remember to tune anything.

Three caps are asserted, and they are marked as such below. They exist because
some failures are not expressible as a ratio: a scanned PDF yields no rows to
take a ratio of, and a filing parsed through the text fallback can look complete
while being read by the weaker of the two paths.

The number is for sorting. The warnings alongside it are what a person acts on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Sequence

# The fields a transaction needs before it can support any analysis in this
# project: when it happened, which way, how much, and what. Ticker is excluded
# deliberately -- plenty of disclosed assets legitimately have none, so counting
# its absence against the parser would punish it for the filing's own content.
REQUIRED_FIELDS = ("transaction_date", "transaction_type", "amount_min", "description")

# --- Asserted caps. Three of them, all visible, all arguable. ---
#
# A filing read through the text fallback can look complete and still be the
# weaker reading: that path has no column structure to check itself against.
TEXT_FALLBACK_CEILING = 0.7
# Column positions guessed rather than read off a header.
NO_HEADER_CEILING = 0.5


@dataclass
class ParseConfidence:
    """The score, and the reasons behind it."""

    confidence: float
    warnings: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str | None:
        return "; ".join(self.warnings) or None


def score_ptr_parse(
    quality: Dict[str, Any],
    transactions: Sequence[Dict[str, Any]],
    filing_date: datetime | None = None,
) -> ParseConfidence:
    """Score one PTR parse from what the parser observed."""
    warnings: List[str] = []

    if not quality.get("text_extracted", False):
        # No text layer at all: a scanned or image-only PDF. Nothing was read,
        # so there is no ratio to take.
        return ParseConfidence(0.0, ["no text layer in PDF - likely a scan"])

    if not transactions:
        # A PTR exists to report transactions. Zero of them is a failed parse,
        # not a quiet filing, and it was previously recorded as a success.
        return ParseConfidence(0.0, ["no transactions found in a periodic transaction report"])

    detected = int(quality.get("rows_detected") or 0)
    parsed = int(quality.get("rows_parsed") or 0)
    row_ratio = (parsed / detected) if detected else 1.0

    dropped = max(detected - parsed, 0)
    if dropped:
        warnings.append(f"{dropped} row(s) looked like transactions but could not be read")

    recovered = int(quality.get("rows_recovered") or 0)
    if recovered:
        warnings.append(
            f"{recovered} row(s) recovered from a cell the PDF failed to split - "
            "read by the weaker text path"
        )

    extracted = sum(
        1 for txn in transactions for name in REQUIRED_FIELDS if _present(txn.get(name))
    )
    expected = len(transactions) * len(REQUIRED_FIELDS)
    field_ratio = (extracted / expected) if expected else 0.0

    for name in REQUIRED_FIELDS:
        missing = sum(1 for txn in transactions if not _present(txn.get(name)))
        if missing:
            warnings.append(f"{missing} transaction(s) missing {name}")

    warnings.extend(_sanity_warnings(transactions, filing_date))

    confidence = row_ratio * field_ratio

    if quality.get("used_text_fallback"):
        warnings.append("no usable tables - parsed from the text layer")
        confidence = min(confidence, TEXT_FALLBACK_CEILING)

    if not quality.get("headers_recognised", True):
        warnings.append("column positions assumed, not read from a header row")
        confidence = min(confidence, NO_HEADER_CEILING)

    return ParseConfidence(round(min(max(confidence, 0.0), 1.0), 4), warnings)


# House Clerk filing types that carry no financial schedule at all, so an empty
# parse of one is the CORRECT outcome and not a failure. Verified by reading the
# documents themselves rather than inferred from the letter:
#
#   X  "FDER", a Financial Disclosure EXTENSION REQUEST -- one page, ~1,000
#      characters, a form letter to the Clerk asking for more time. 203 of them
#      were recorded as "no assets or liabilities found in an annual filing",
#      which made them the single largest category of apparent parser failure in
#      the corpus. They are not annual filings and have nothing to find.
#   D, W  one-page letters to the Clerk under the same "CNRFDR" heading.
#
# Deliberately NOT here: O, A, H and T. Those are real multi-page FDRs -- Rosa
# DeLauro's is 4 pages and 4,651 characters -- and the 28 of them that yielded
# nothing are genuine parser gaps that must keep reporting themselves as such.
NO_FINANCIAL_SCHEDULE = frozenset({"X", "D", "W"})


def score_filing_with_no_schedule(filing_type: str) -> ParseConfidence:
    """A filing that was never going to contain assets or transactions.

    Scored 1.0 because the parse is complete: everything the document has to
    give has been taken from it. Recording 0.0 instead did real damage -- it
    put 211 letters into the "needs attention" queue, set `parse_error` on
    them, and made the project's own measure of parser quality read far worse
    than the parser deserved. A number that counts extension requests as
    failures cannot be used to decide whether parsing is improving.
    """
    return ParseConfidence(1.0, [])


def score_fd_parse(
    text_extracted: bool,
    assets: int,
    liabilities: int,
    errors: Sequence[str] = (),
    rows_detected: int | None = None,
    discloses_no_rows: bool = False,
) -> ParseConfidence:
    """Score an annual FD parse, by how much of it was actually read.

    This was binary -- 1.0 unless the parse produced nothing -- and its docstring
    defended that as "deliberately coarser", on the grounds that annual filings
    "carry no per-row structure this project depends on". Both halves were
    wrong. Net worth is a sum over those rows, and the detectors built on it
    publish dollar figures about named members of Congress.

    What that cost: every one of the 927 House annual filings in the corpus
    reported confidence 1.0, while capturing roughly half of Schedule A.
    Measured against the documents --

        Carter   (10066714)   48 holdings in the text, 22 stored   46%
        Moulton  (10067208)  103 holdings in the text, 33 stored   32%
        Davidson (10067467)   26 holdings in the text, 13 stored   50%

    -- and the rows lost are not a random half. The largest holdings are the
    ones pdfplumber most often fails to see as table rows, so the loss is
    systematically biased toward understating wealth. Carter's stored net worth
    tops out at $1,000,000 while his filing discloses "Guardian Point Capital
    [HE] $5,000,001 - $25,000,000".

    A confidence that cannot fall is not a measurement. It is also why this hid
    for so long: every downstream guard -- `--min-confidence`, the opacity
    index, the parsed-data page -- trusted it and saw nothing wrong.

    `rows_detected` is the independent count, from `count_schedule_a_rows`. It
    is optional so callers that genuinely have no document to count against
    (tests, and the Senate path, which produces no assets at all today) keep the
    old behaviour rather than being scored against zero.

    `discloses_no_rows` is the other independent reading of the document, from
    `pdf_parser.discloses_no_rows`: the filing PRINTS "None disclosed." under
    Schedules A, B and D. Then nothing stored is everything there was, and the
    parse is complete. Without it, seven House annuals -- Frost, Boebert,
    Crawford and Valadao, who between them hold nothing this project stores --
    were recorded at 0.0 with "no assets or liabilities found in an annual
    filing", the same sentence a real failure gets, and were re-downloaded from
    the Clerk by every cleanup pass to arrive at the same answer. Same mistake
    as scoring an extension request against the annual rubric; see
    `score_filing_with_no_schedule`.
    """
    warnings: List[str] = []
    if errors:
        warnings.extend(errors)
    if not text_extracted:
        return ParseConfidence(0.0, ["no text layer in PDF - likely a scan"])
    if not assets and not liabilities:
        # `errors` still wins: a filing that says "None disclosed." AND failed
        # partway through was not read completely, whatever it says.
        if discloses_no_rows and not errors:
            return ParseConfidence(
                1.0, ["the filing discloses no assets, transactions or liabilities"]
            )
        warnings.append("no assets or liabilities found in an annual filing")
        return ParseConfidence(0.0, warnings)
    if errors:
        return ParseConfidence(0.0, warnings)

    if not rows_detected:
        return ParseConfidence(1.0, warnings)

    captured = min(assets / rows_detected, 1.0)
    if assets < rows_detected:
        warnings.append(
            f"stored {assets} of {rows_detected} Schedule A holdings the document names "
            f"({captured:.0%}); the rest are in the text layer and in no table, and the "
            "largest holdings are the likeliest to be missing"
        )
    return ParseConfidence(round(captured, 4), warnings)


def _present(value: Any) -> bool:
    """Whether a field was actually extracted.

    Zero is a real amount and an empty string is not a description, so this
    cannot be a plain truthiness test.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _sanity_warnings(
    transactions: Sequence[Dict[str, Any]], filing_date: datetime | None
) -> List[str]:
    """Contradictions that mean a field was read from the wrong place."""
    warnings: List[str] = []

    if filing_date is not None:
        # A trade cannot happen after the report of it was filed. This is the
        # exact shape the notification-date bug took: the parser was reading the
        # column beside the one it wanted, and the dates came out later than
        # they should have been.
        after_filing = sum(
            1
            for txn in transactions
            if isinstance(txn.get("transaction_date"), datetime)
            and txn["transaction_date"] > filing_date
        )
        if after_filing:
            warnings.append(
                f"{after_filing} transaction(s) dated after the filing that reports them"
            )

    inverted = sum(
        1
        for txn in transactions
        if txn.get("amount_min") is not None
        and txn.get("amount_max") is not None
        and txn["amount_min"] > txn["amount_max"]
    )
    if inverted:
        warnings.append(f"{inverted} transaction(s) with an inverted amount band")

    return warnings
