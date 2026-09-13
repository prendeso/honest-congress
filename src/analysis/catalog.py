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
    ),
    Detector(
        "lobbying_overlap",
        "Lobbying Overlap",
        "The member traded a company's stock around the time that company "
        "disclosed lobbying Congress.",
        "Lobbying disclosures name the issuer, not the member lobbied. This "
        "cannot show the member was among them.",
        f"{PTR} joined to Senate LDA lobbying filings",
    ),
    Detector(
        "contract_front_run",
        "Contract Front-Run",
        "The member bought a company's stock shortly before that company was "
        "awarded a federal contract.",
        "Purchases only, and many awards are publicly anticipated long before "
        "they are announced. Buying ahead of an expected award is not buying "
        "ahead of a secret one, and this cannot tell the two apart.",
        f"{PTR} joined to USASpending federal award actions",
    ),
    Detector(
        "sponsorship_conflict",
        "Sponsorship Conflict",
        "The member traded in a sector around sponsoring a bill whose policy area covers it.",
        "The match is bill policy area to sector, which is coarse: a bill can "
        "touch an industry without affecting any particular company in it.",
        f"{PTR} joined to Congress.gov sponsored bills",
    ),
    Detector(
        "bill_jurisdiction_conflict",
        "Committee Bill Conflict",
        "The member traded in a sector around a bill in that sector reaching a "
        "committee they sit on.",
        "A committee sees a great many bills, and a seat on it is not knowledge "
        "of any one of them.",
        f"{PTR} joined to Congress.gov bill referrals and committee rosters",
    ),
    Detector(
        "cross_member_cluster",
        "Cross-Member Cluster",
        "Several members traded the same stock the same way within a short window of each other.",
        "Members read the same news. A cluster is a coincidence in timing, not "
        "evidence of coordination, and widely held stocks cluster by nature.",
        PTR,
    ),
    Detector(
        "committee_jurisdiction_conflict",
        "Committee Jurisdiction",
        "The member traded in a sector their committee oversees.",
        "This is a standing state of affairs, not an event: it says nothing "
        "about the timing of any trade, which is why it carries no q-value.",
        f"{PTR} joined to committee assignments",
    ),
    Detector(
        "large_trade",
        "Large Trade",
        "A single disclosed trade large in absolute terms.",
        BAND_LIMIT,
        PTR,
    ),
    Detector(
        "volume_spikes",
        "Volume Spike",
        "A trade much larger than this member usually files.",
        f"{BAND_LIMIT} A member who files rarely has little to be unusual against.",
        PTR,
    ),
    Detector(
        "high_trading_frequency",
        "High Trading Frequency",
        "The member files far more trades than most members do.",
        "Trading often is not trading improperly, and a managed account can "
        "produce this without the member choosing any of it.",
        PTR,
    ),
    Detector(
        "trade_clustering",
        "Trade Clustering",
        "Several trades filed close together, in the same direction.",
        "Filings are batched by deadline, so trades cluster in the paperwork "
        "whether or not they clustered in fact.",
        PTR,
    ),
    Detector(
        "sector_concentration",
        "Sector Concentration",
        "Much of one filing's trading falls in a single industry sector.",
        "Sector comes from matching the description text, and committee "
        "assignments are not consulted here at all.",
        PTR,
    ),
    Detector(
        "late_filing",
        "Late Filing",
        "The trade was reported more than the 45 days the STOCK Act allows.",
        "The only item here that is a rule violation on its face. The filing "
        "date is the Clerk's, and an amended filing can make a timely report "
        "look late.",
        PTR,
    ),
    Detector(
        "wealth_vs_salary",
        "Wealth vs Salary",
        "Reported net worth grew by more than congressional salary alone would explain.",
        "Assets and liabilities are disclosed as bands, so this is an estimate "
        "built on estimates. Spouse income, inheritance and investment returns "
        "are all outside it.",
        FD,
    ),
    Detector(
        "excessive_wealth_growth",
        "Excessive Wealth Growth",
        "Reported net worth grew unusually fast year over year.",
        "Same band arithmetic as above, and a market that rose is not a member who did anything.",
        FD,
    ),
    Detector(
        "rapid_asset_appreciation",
        "Rapid Asset Appreciation",
        "One disclosed asset more than doubled in reported value in a year.",
        "A band boundary crossed by a dollar reports the same jump as a real "
        "one, and an asset can be re-categorised between filings.",
        FD,
    ),
    Detector(
        "multi_factor_risk",
        "Multi-Factor",
        "The member appears in three or more of the other categories.",
        "A compound of the others, so it inherits every limit above and adds "
        "one: the categories are not independent, and a single busy trader "
        "triggers several of them at once.",
        "Combination of the above",
    ),
]


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
            # Whether a q-value exists for this type at all. Null q on a type
            # that has a null model means the finding did not survive; null q
            # on one that does not means no test was ever run. Conflating those
            # is the single easiest way to misread this data.
            "has_null_model": d.anomaly_type not in NO_NULL_MODEL,
        }
        for d in live_detectors()
    ]
