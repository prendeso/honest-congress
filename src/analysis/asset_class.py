"""What kind of instrument was traded, when the document says so.

`Transaction` has no `asset_type` column -- that lives on `Asset`, which holds
Schedule A positions, not PTR rows. So every PTR transaction reached the
detectors as an unlabelled trade, and `high_trading_frequency` described all of
them the same way:

    "18 stock trades were disclosed in March 2024"

Sen. Rick Scott made zero. Every one was a municipal bond. Same sentence about
Sen. Warner's eighteen, and about Sen. Fetterman's, which were bonds in a
child's account. The count was right and the noun was invented.

**The form says which is which.** The House PTR prints an asset-class code in
brackets at the end of the asset name -- `[ST]`, `[GS]`, `[OP]`, `[CT]` -- and
`ptr_parser` already carries it through in the description.

Only two codes are asserted here, and both are grounded in the corpus's own
descriptions rather than in a legend (the PTRs do not print one):

    [GS]  every row carrying it names a government or municipal security --
          "US Treasury Bill 912797GD3 [GS]", "U.S. Treasury Bond [GS]",
          "Chicago Ill O'Hare 4.00% 01/01/36 [GS]"
    [ST]  every row carrying it names a share -- "Albemarle Corporation (ALB)
          [ST]", "Microsoft Corporation - Common Stock (MSFT) [ST]"

Everything else stays unclassified. A code nobody has read a document for does
not get a meaning here, the same rule `SCHEDULE_H_FILING_TYPES` follows.

**The fallback, and why it is conservative.** 51.8% of rows in that corpus have
no bracket code at all, because the weaker text path truncates the description
before the trailing `[XX]`. For those, and only those, the wording below is
read. Measured against the rows that DO carry a code:

    [GS] matched   247 of 383   (64% recall)
    [ST] matched     1 of 4,371 (0.02%) -- "Vanguard Short-Term Corporate
                                 Bond ETF (VCSH) [ST]", which is a bond fund

So it under-claims rather than over-claims, which is the right direction: a
missed bond leaves a finding worded as it is today, while a misclassified share
would put a false noun in a sentence about a named person.

A bare coupon rate was tested as an extra signal and rejected. It lifts [GS]
recall to 74% but drags in 13 preferred-share rows -- "Ellington Financial Inc.
7.00% Series D Cumulative Perpetual Redeemable Preferred" -- which the filers
coded [ST]. Equity that pays a coupon is not the thing this is trying to name.
"""

from __future__ import annotations

import re
from typing import Sequence

# The last bracketed pair in the description; the asset class sits at the end
# of the printed name, after any ticker.
_CLASS_CODE = re.compile(r"\[([A-Z]{2})\]")

GOVERNMENT_SECURITY = "GS"
STOCK = "ST"

# Read only when the description carries no bracket code. Deliberately narrow:
# each of these names the instrument outright.
_FIXED_INCOME_WORDING = re.compile(
    r"treasury|municipal|\bmuni\b|\bbond\b|\bdebenture\b|\bt-bill\b"
    r"|certificate of deposit|savings bond"
    r"|\bnote due\b|\b(due|matures|maturity)\b\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
    re.IGNORECASE,
)


def class_code(transaction) -> str | None:
    """The asset-class code the form printed, or None if it was not read."""
    codes = _CLASS_CODE.findall(getattr(transaction, "description", None) or "")
    return codes[-1] if codes else None


def is_fixed_income(transaction) -> bool:
    """Whether the document identifies this as debt rather than equity.

    The printed code decides when there is one. The wording is consulted only
    when there is not, so a row the form labelled `[ST]` can never be talked
    out of being a share by a word in its name.
    """
    code = class_code(transaction)
    if code is not None:
        return code == GOVERNMENT_SECURITY
    return bool(_FIXED_INCOME_WORDING.search(getattr(transaction, "description", None) or ""))


def all_fixed_income(transactions: Sequence) -> bool:
    """Whether every row here is debt -- the case that makes "stock" a lie."""
    return bool(transactions) and all(is_fixed_income(t) for t in transactions)


def fixed_income_count(transactions: Sequence) -> int:
    return sum(1 for t in transactions if is_fixed_income(t))
