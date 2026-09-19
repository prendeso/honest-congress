"""Whose trade is it -- the question every count-based finding answers wrongly.

A financial disclosure is a HOUSEHOLD document. The House PTR form prints an
owner in its own column -- `SP` spouse, `JT` joint, `DC` dependent child, blank
for the filer -- because the law requires a member to report their spouse's and
dependent children's transactions alongside their own. Reporting them is the
member's duty. Having made them is not their act.

Nothing in this codebase acted on that column. `grep -rn owner src/analysis`
found it in exactly two roles: a component of the restatement content key, and
a label carried through a projection. No detector has ever filtered on it. So
every finding of the form "Member made N trades" counted the whole household
and published the total under one person's name.

Measured over a 10,594-transaction corpus of real House PTRs, after the owner
codes were read correctly (#98):

    Self               4,550   42.9%
    Spouse             3,094   29.2%
    Joint              2,527   23.9%
    Dependent Child      423    4.0%

    not the member's own: 6,044 (57.1%)

The consequences were published about named people. Sen. Hagerty's four
"unusual volume spikes" are four rows the parser stored CORRECTLY as
`Dependent Child` -- #98 did not touch them, because nothing was misread. They
were counted as his because no code asked. Rep. McClain Delaney was flagged for
"17 trades" of which she made none.

**Joint counts, spouse and dependent child do not.** `JT` means held jointly by
the filer and their spouse: the member is a party to it, so excluding it would
understate their own position. `SP` and `DC` are holdings the member reports but
does not hold. That line is drawn conservatively on purpose -- it removes from
an accusation only what the member has no stake in.

An unset owner is the member's. On the House form the owner column is blank for
the filer, so blank means "the filer", not "unknown". That is the document's
convention, not an assumption this module makes.

**Where this must NOT be applied**, and why each is deliberate:

* `compliance.py` / `late_filing` -- the STOCK Act deadline is the MEMBER's
  obligation for every reportable trade, their spouse's included. Filtering
  here would erase real late filings. This is also the one detector that
  survived the adversarial audit, and it survives untouched.
* `opacity.py` -- measures how completely a filing was disclosed. Every row the
  member had to report belongs in that denominator.

Both are asserted in tests, so a later caller cannot quietly apply the filter
there.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, TypeVar

# Exactly the vocabulary `_normalize_owner` emits in `ptr_parser.py` and
# `senate_html_parser.py`. A value from outside it is treated as NOT the
# member's: an owner nobody has taught this module to read is not grounds for
# naming someone in a finding.
OWNERS_THE_MEMBER_HOLDS = frozenset({"Self", "Joint"})

SPOUSE = "Spouse"
DEPENDENT_CHILD = "Dependent Child"

_UNSET = ""

Row = TypeVar("Row")


def owner_of(transaction) -> str:
    """The stored owner, normalised for comparison.

    Blank or NULL is the filer, per the form's own convention.
    """
    value = (getattr(transaction, "owner", None) or _UNSET).strip()
    return value or "Self"


def held_by_member(transaction) -> bool:
    """Whether the member is a party to this holding."""
    return owner_of(transaction) in OWNERS_THE_MEMBER_HOLDS


def trades_the_member_holds(transactions: Sequence[Row]) -> List[Row]:
    """The subset a finding may attribute to the member personally.

    Order is preserved, so a caller that sorted by date stays sorted.
    """
    return [t for t in transactions if held_by_member(t)]


def owner_breakdown(transactions: Sequence) -> Dict[str, int]:
    """How the rows divide by owner, for a finding's evidence block.

    The excluded rows are disclosed, not hidden: a finding that dropped a
    spouse's trades from its count should say how many it dropped, so a reader
    can see the household figure the member actually filed.
    """
    counts: Dict[str, int] = {}
    for transaction in transactions:
        owner = owner_of(transaction)
        counts[owner] = counts.get(owner, 0) + 1
    return counts
