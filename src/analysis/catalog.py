"""What each detector actually looks for, in one place the site can read.

The dashboard kept its own copy of this: a legend, a filter list, and three
JavaScript lookup tables, all hand-maintained. They drifted, and the drift was
not cosmetic. The page was still advertising

    Stock Outperformance -- "Trading returns significantly beat S&P 500"
    Trade Timing         -- "Perfect timing ... buying before stock rises"
    Loss Avoidance       -- "Statistically improbable success rate (80%+)"

to visitors, naming real people, after all three detectors had been disabled
for making exactly those claims without a price series or a statistical test
(D5, D10). Meanwhile the six detectors that *do* carry a q-value -- the
strongest findings the system produces -- were not in the legend, the filter,
or the explanation table at all, so each one rendered as "This anomaly requires
further investigation to understand its significance."

So the page no longer keeps a copy. `/api/anomalies/types` serves this module,
and what a visitor reads comes from the same declarations the detectors are
registered in: `NO_NULL_MODEL` decides whether a type is marked as carrying a
q-value, and `get_settings().disabled_anomaly_types_set` decides whether it is served
at all. A detector cannot be disabled and still advertised.

`means` is what the detector measures, stated so it cannot be read as a finding
of wrongdoing. `limits` is what it cannot see, which is the part a reader needs
most and the part a legend usually leaves out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from src.analysis.significance import NO_NULL_MODEL
from src.config import get_settings

PTR = "Periodic Transaction Reports (STOCK Act trade filings)"
FD = "Annual Financial Disclosures"
BAND_LIMIT = (
    "Amounts are disclosed as bands, never exact figures, so every size here is a band midpoint."
)


@dataclass(frozen=True)
class Detector:
    """One anomaly type as a reader of the site should understand it."""

    anomaly_type: str
    name: str
    means: str
    limits: str
    source: str
    # What `computed_value` and `threshold_value` are counted in. Declared
    # here, beside the detector, because the page cannot work it out: it used
    # to guess from the type NAME, and `excessive_wealth_growth` stores a
    # dollar amount while its name contains "growth", so the site published
    # a $2,625,000 rise in net worth as "2625000.0%" under a named member --
    # all seven of that detector's findings, every one of them.
    #
    # Both fields always share a unit: each detector's threshold is the same
    # quantity its computed value is measured against (days against a window
    # in days, dollars against a salary in dollars), which is why one
    # declaration covers the pair.
    #
    # It has no default on purpose. A new detector cannot be added without
    # saying what its numbers mean.
    value_unit: str


# Ordered as the page shows them: the ones a null model can be built for
# first, because those are the findings that survive a test rather than a
# threshold.
DETECTORS: List[Detector] = [
    Detector(
        "donor_conflict",
        "Donor Conflict",
        "The member traded a company's stock close to that company's PAC or its "
        "employees donating to their campaign.",
        "A donation and a trade near each other is a coincidence until something "
        "else says otherwise. Nothing here shows the member knew of the donation "
        "when they traded.",
        f"{PTR} joined to FEC campaign finance filings",
        value_unit="days",
    ),
    Detector(
        "lobbying_overlap",
        "Lobbying Overlap",
        "The member traded a company's stock around the time that company "
        "disclosed lobbying Congress.",
        "Lobbying disclosures name the issuer, not the member lobbied, so this "
        "cannot show the member was among them. It is also the most common "
        "finding on this site by a wide margin, and that is a property of "
        "lobbying rather than of trading: a large company files quarterly, "
        "often through several registrants, and each filing opens a window "
        "either side of it. For a company that lobbies continuously those "
        "windows cover most of the year, so almost any trade in its stock "
        "falls inside one. Read the q-value, not the count.",
        f"{PTR} joined to Senate LDA lobbying filings",
        value_unit="days",
    ),
    Detector(
        "contract_front_run",
        "Contract Front-Run",
        "The member bought a company's stock shortly before that company was "
        "awarded a federal contract.",
        "Purchases only, and many awards are publicly anticipated long before "
        "they are announced. Buying ahead of an expected award is not buying "
        "ahead of a secret one, and this cannot tell the two apart. Coverage is "
        "also partial by construction: the award set is the largest hundred "
        "federal award actions to each company anyone in Congress has traded, so "
        "a smaller award to a heavily contracted company is not in it, and an "
        "award booked to a subsidiary the SEC register does not tie back to its "
        "parent is missing entirely. An absence here is not evidence of none. "
        "Actions that take money back off a contract, and modifications that "
        "move no money at all, are excluded: they are in the federal feed but "
        "they are not an award being made.",
        f"{PTR} joined to USASpending federal award actions",
        value_unit="days",
    ),
    Detector(
        "sponsorship_conflict",
        "Sponsorship Conflict",
        "The member traded in a sector around sponsoring a bill whose policy area covers it.",
        "The match is bill policy area to sector, which is coarse: a bill can "
        "touch an industry without affecting any particular company in it.",
        f"{PTR} joined to Congress.gov sponsored bills",
        value_unit="days",
    ),
    Detector(
        "bill_jurisdiction_conflict",
        "Committee Bill Conflict",
        "The member traded in a sector around a bill in that sector reaching a "
        "committee they sit on.",
        "A committee sees a great many bills, and a seat on it is not knowledge "
        "of any one of them.",
        f"{PTR} joined to Congress.gov bill referrals and committee rosters",
        value_unit="days",
    ),
    Detector(
        "cross_member_cluster",
        "Cross-Member Cluster",
        "Several members traded the same stock the same way within a short window of each other.",
        "Members read the same news. A cluster is a coincidence in timing, not "
        "evidence of coordination, and widely held stocks cluster by nature.",
        PTR,
        value_unit="count",
    ),
    Detector(
        "committee_jurisdiction_conflict",
        "Committee Jurisdiction",
        "The member traded in a sector their committee oversees.",
        "This is a standing state of affairs, not an event: it says nothing "
        "about the timing of any trade, which is why it carries no q-value.",
        f"{PTR} joined to committee assignments",
        value_unit="percent",
    ),
    Detector(
        "large_trade",
        "Large Trade",
        "A single disclosed trade large in absolute terms.",
        BAND_LIMIT,
        PTR,
        value_unit="dollars",
    ),
    Detector(
        "volume_spikes",
        "Volume Spike",
        "A trade much larger than this member usually files.",
        f"{BAND_LIMIT} A member who files rarely has little to be unusual against.",
        PTR,
        value_unit="dollars",
    ),
    Detector(
        "high_trading_frequency",
        "High Trading Frequency",
        "The member files far more trades than most members do.",
        "Trading often is not trading improperly, and a managed account can "
        "produce this without the member choosing any of it.",
        PTR,
        value_unit="count",
    ),
    Detector(
        "trade_clustering",
        "Trade Clustering",
        "Several trades filed close together, in the same direction.",
        "Filings are batched by deadline, so trades cluster in the paperwork "
        "whether or not they clustered in fact.",
        PTR,
        value_unit="count",
    ),
    Detector(
        "sector_concentration",
        "Sector Concentration",
        "Much of one filing's trading falls in a single industry sector.",
        "Sector comes from matching the description text, and committee "
        "assignments are not consulted here at all.",
        PTR,
        value_unit="percent",
    ),
    Detector(
        "late_filing",
        "Late Filing",
        "The trade was reported more than the 45 days the STOCK Act allows.",
        "The only item here that is a rule violation on its face. The filing "
        "date is the Clerk's, and an amended filing can make a timely report "
        "look late. Over half of these sit on a trade the form attributes to "
        "the member's spouse or a dependent child -- the deadline is still the "
        "member's, and each finding now says whose trade it was.",
        PTR,
        value_unit="days",
    ),
    Detector(
        "wealth_vs_salary",
        "Wealth vs Salary",
        "Reported net worth grew by more than congressional salary alone would explain.",
        "Assets and liabilities are disclosed as bands, so this is an estimate "
        "built on estimates. Spouse income, inheritance and investment returns "
        "are all outside it.",
        FD,
        value_unit="dollars",
    ),
    Detector(
        "excessive_wealth_growth",
        "Excessive Wealth Growth",
        "Reported net worth grew unusually fast year over year.",
        "Same band arithmetic as above, and a market that rose is not a member who did anything.",
        FD,
        value_unit="dollars",
    ),
    Detector(
        "rapid_asset_appreciation",
        "Rapid Asset Appreciation",
        "One disclosed asset more than doubled in reported value in a year.",
        "A band boundary crossed by a dollar reports the same jump as a real "
        "one, and an asset can be re-categorised between filings.",
        FD,
        value_unit="percent",
    ),
    Detector(
        "multi_factor_risk",
        "Multi-Factor",
        "The member appears in three or more of the other categories.",
        "A compound of the others, so it inherits every limit above and adds "
        "one: the categories are not independent, and a single busy trader "
        "triggers several of them at once.",
        "Combination of the above",
        value_unit="score",
    ),
]


def type_has_null_model(anomaly_type: str) -> bool:
    """Whether a q-value can exist for this kind of finding at all.

    A property of the detector, not of any one finding. Six detectors ask about
    a coincidence in timing and have a null to shuffle; the other eleven measure a
    magnitude and have none.

    The distinction matters because a missing q-value means different things on
    either side of it, and `src.analysis.significance` assigns NULL in both
    cases: "a magnitude rule with no null to shuffle, or testable in principle
    but not in this run -- too short a span, no eligible trades". Reading the
    detector off the finding's own q-value, which is what the API used to do,
    collapses the two and reports the second as the first -- telling a reader
    that no test exists for a category that is tested.
    """
    return anomaly_type not in NO_NULL_MODEL


def live_detectors() -> List[Detector]:
    """Every detector whose findings can actually be served.

    Reads the same disabled-types setting the query layer does, so a detector
    turned off in configuration disappears from the site's own explanation of
    itself rather than being described to visitors as something it produces.
    """
    off = get_settings().disabled_anomaly_types_set
    return [d for d in DETECTORS if d.anomaly_type not in off]


def as_dicts() -> List[Dict[str, object]]:
    """The catalogue in the shape the API serves and the page renders."""
    return [
        {
            "anomaly_type": d.anomaly_type,
            "name": d.name,
            "means": d.means,
            "limits": d.limits,
            "source": d.source,
            "value_unit": d.value_unit,
            # Whether a q-value exists for this type at all. Null q on a type
            # that has a null model means the finding did not survive; null q
            # on one that does not means no test was ever run. Conflating those
            # is the single easiest way to misread this data.
            "has_null_model": type_has_null_model(d.anomaly_type),
        }
        for d in live_detectors()
    ]
