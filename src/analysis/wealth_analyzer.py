"""Wealth anomaly analyzer for congressional disclosures."""

import logging
from decimal import Decimal
from typing import Any, Dict, List, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.analysis.anomaly_key import find_existing, identity_of
from src.config import get_settings
from src.db import Anomaly, Asset, Disclosure, Liability, Member

logger = logging.getLogger(__name__)

# How often each analyzer says where it has got to. Both of these ran in
# complete silence: the gap between `analyze` starting and the first detector
# logging anything was 10m42s in the production run of 2026-09-14, and nothing
# in the log said which of the two it was, or whether either was moving.
PROGRESS_EVERY_MEMBERS = 100

# Filings that are NOT a snapshot of a sitting member's own finances, and so
# must never become a baseline in the net-worth series.
#
# A CANDIDATE REPORT is the dangerous one. It is filed by somebody running for
# the seat, before they hold it, on a different schedule -- so it describes a
# private citizen's finances, and comparing it against their first member annual
# measures the change of form, not a change of wealth. Measured live, that
# produced two published accusations against sitting members:
#
#   Craig Goldman  published $15,008,502 of growth; against his own 2024 House
#                  annual the figure is $551,001
#   Laura Gillen   published $476,500; against her own annual it is $75,499 --
#                  below the salary threshold, so it should not have fired at all
#
# Stored as bare "C" on the House side and as free text on the Senate side, so
# both spellings are covered.
#
# This cannot reuse `house._NOT_A_MEMBER_FILING`: that set is keyed to the
# Clerk's INDEX vocabulary, where a periodic transaction report is "P". Stored
# rows spell it "PTR", and the Senate spells everything in prose. Two different
# vocabularies, deliberately not conflated.
_NOT_A_NET_WORTH_SNAPSHOT = ("c",)
_NOT_A_NET_WORTH_PREFIX = ("candidate report",)


def is_net_worth_snapshot(disclosure: Disclosure) -> bool:
    """Whether this filing may stand as a member's net worth for its year.

    A negative test rather than a list of permitted types: the corpus carries 33
    distinct `filing_type` values across two chambers, House single letters and
    Senate free text, and an allow-list would silently drop every type nobody
    thought of -- which is the failure mode that left `filing_type == "FD"`
    matching zero rows for months.
    """
    if disclosure.is_ptr:
        return False
    label = (disclosure.filing_type or "").strip().lower()
    if label in _NOT_A_NET_WORTH_SNAPSHOT:
        return False
    return not label.startswith(_NOT_A_NET_WORTH_PREFIX)


def net_worth_snapshot_clause():
    """`is_net_worth_snapshot` as a SQL predicate, for counting without loading.

    The same rule twice is a liability -- this project has been bitten more than
    once by two lists that drifted -- so
    `tests/test_wealth_baseline.py::TestTheTwoSpellingsOfTheRuleAgree` asserts
    the Python predicate and this clause classify every stored filing
    identically. Change one, and that test names the other.
    """
    from sqlalchemy import func, not_, or_

    label = func.lower(func.trim(func.coalesce(Disclosure.filing_type, "")))
    excluded = [label == value for value in _NOT_A_NET_WORTH_SNAPSHOT]
    excluded += [label.startswith(prefix) for prefix in _NOT_A_NET_WORTH_PREFIX]
    return (Disclosure.is_ptr == False) & not_(or_(*excluded))  # noqa: E712


settings = get_settings()


class WealthAnalyzer:
    """
    Analyzes congressional member wealth for anomalies.

    Detects:
    - Net worth growth exceeding congressional salary by threshold
    - Sudden unexplained asset increases
    - Discrepancies between reported assets and lifestyle indicators
    """

    def __init__(
        self, threshold_percent: float | None = None, congressional_salary: int | None = None
    ):
        self.threshold_percent = threshold_percent or settings.wealth_growth_threshold_percent
        self.congressional_salary = congressional_salary or settings.congressional_salary

    def analyze_member(
        self, db: Session, member_id: int, member: Member | None = None
    ) -> List[Dict[str, Any]]:
        """
        Analyze a single member for wealth anomalies.

        Args:
            db: Database session
            member_id: Member ID to analyze
            member: the already-loaded row, when the caller has it

        Returns:
            List of detected anomalies
        """
        # Same as TradeAnalyzer.analyze_member: the roster walk above already
        # holds this row, and fetching it back costs one round trip per member.
        if member is None:
            member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            return []

        anomalies = []

        # Ordered by year, then by WHEN IT WAS FILED, then by a unique key.
        #
        # `filing_year` alone is not an order. The growth loop below compares
        # consecutive entries and skips any pair inside one year, so the only
        # comparison that survives is LAST-filing-of-a-year against
        # first-of-the-next -- which makes "last of the year" the baseline, and
        # `ORDER BY filing_year` leaves that unspecified. Members here hold up to
        # six filings in 2024 whose net worths differ by millions, so the
        # published figure depended on which row the database happened to return
        # last. Same defect as the unstable pagination order, with a worse
        # consequence: this one names a person and a dollar amount.
        #
        # Filing date first, because within a year the later filing supersedes
        # the earlier -- an amendment restates the original in full. `id` last,
        # because `filing_date` is nullable and ties must still break.
        disclosures = [
            disclosure
            for disclosure in (
                db.query(Disclosure)
                .filter(Disclosure.member_id == member_id, Disclosure.parsed == True)
                .order_by(Disclosure.filing_year, Disclosure.filing_date, Disclosure.id)
                .all()
            )
            if is_net_worth_snapshot(disclosure)
        ]

        if len(disclosures) < 2:
            return []  # Need at least 2 years to compare

        # Two queries for this member's filings, not two per filing.
        totals = self._net_worth_totals(db, [d.id for d in disclosures])

        # Calculate net worth for each year
        net_worths = []
        for disclosure in disclosures:
            net_worth = self._calculate_net_worth(db, disclosure.id, totals=totals)
            net_worths.append(
                {
                    "year": disclosure.filing_year,
                    "disclosure_id": disclosure.id,
                    "net_worth_min": net_worth["min"],
                    "net_worth_max": net_worth["max"],
                    "net_worth_mid": net_worth["mid"],
                }
            )

        # Check year-over-year growth
        for i in range(1, len(net_worths)):
            prev = net_worths[i - 1]
            curr = net_worths[i]

            if prev["net_worth_mid"] and curr["net_worth_mid"]:
                prev_nw = float(prev["net_worth_mid"])
                curr_nw = float(curr["net_worth_mid"])

                if prev_nw > 0:
                    growth = curr_nw - prev_nw
                    growth_percent = (growth / prev_nw) * 100

                    # Calculate expected maximum growth from salary
                    years_between = curr["year"] - prev["year"]

                    # Skip if no time has passed or only 0 years between (insufficient data)
                    if years_between <= 0:
                        continue

                    max_salary_growth = float(self.congressional_salary) * years_between
                    salary_growth_percent = (max_salary_growth / prev_nw) * 100

                    # Flag if growth exceeds salary by threshold
                    if growth > 0 and growth_percent > (
                        salary_growth_percent + float(self.threshold_percent)
                    ):
                        # Use vague ranges for growth percentage
                        if growth_percent < 100:
                            growth_range = "significantly"
                        elif growth_percent < 200:
                            growth_range = "substantially (100-200%)"
                        elif growth_percent < 500:
                            growth_range = "dramatically (200-500%)"
                        else:
                            growth_range = "extremely (over 500%)"

                        # Use vague ranges for dollar amounts
                        if growth < 100000:
                            growth_amount = "tens of thousands of dollars"
                        elif growth < 500000:
                            growth_amount = "$100,000-$500,000"
                        elif growth < 1000000:
                            growth_amount = "$500,000-$1,000,000"
                        elif growth < 5000000:
                            growth_amount = "$1-$5 million"
                        else:
                            growth_amount = "more than $5 million"

                        # Report as anomaly for the CURRENT year (the year with growth)
                        anomaly = {
                            "member_id": member_id,
                            "disclosure_id": curr["disclosure_id"],
                            "anomaly_type": "excessive_wealth_growth",
                            "severity": self._calculate_severity(
                                growth_percent, salary_growth_percent
                            ),
                            # The wording names the filings compared, not just
                            # the years. Which filing served as the baseline was
                            # the whole defect here -- a candidate report could
                            # win the role and nothing on the page said so -- and
                            # a claim about someone's wealth should say what it
                            # was measured against.
                            #
                            # It also earns the finding a new identity.
                            # `anomaly_key` keys a member-level row on its TITLE,
                            # so a corrected finding landing in the same growth
                            # bucket and year pair as a stale one would be
                            # silently DISCARDED by `find_existing` and the wrong
                            # dollar figure served for ever. `_SUPERSEDED_WORDING`
                            # carries the matching entry that deletes the old rows.
                            "title": (
                                f"Wealth growth {growth_range} exceeds salary-based expectation "
                                f"({prev['year']}-{curr['year']} annual filings)"
                            ),
                            "description": (
                                f"Between the {prev['year']} and {curr['year']} annual filings, "
                                f"net worth grew by approximately {growth_amount}. "
                                f"{years_between} year(s) of congressional salary does not account "
                                f"for that. Both figures are midpoints of the reported bands, and "
                                f"candidate reports are excluded from the comparison."
                            ),
                            "computed_value": Decimal(str(growth)),
                            "threshold_value": Decimal(str(max_salary_growth)),
                        }
                        anomalies.append(anomaly)

        return anomalies

    def analyze_all_members(self, db: Session) -> Dict[str, Any]:
        """
        Analyze all members for wealth anomalies.

        Args:
            db: Database session

        Returns:
            Summary of analysis with all detected anomalies
        """
        from src.config import get_settings

        disabled_types = get_settings().disabled_anomaly_types_set

        # `analyze_member` needs at least two parsed filings to compare, and
        # returns [] otherwise. Asking the database which members have that
        # replaces a walk of the entire roster -- two queries each to find out
        # there is nothing to compare. Same condition, so the same members are
        # analysed; see the note in TradeAnalyzer.analyze_all_members.
        comparable = (
            db.query(Disclosure.member_id)
            .filter(Disclosure.parsed == True)  # noqa: E712
            .group_by(Disclosure.member_id)
            .having(func.count(Disclosure.id) >= 2)
        )
        member_ids = [row[0] for row in comparable]
        members = (
            db.query(Member).filter(Member.id.in_(member_ids)).all() if member_ids else []
        )  # Include all members (active + retired)

        all_anomalies = []
        members_analyzed = 0
        members_with_anomalies = 0

        seen: set[tuple] = set()
        # Deliberately NOT using `stored_by_identity` here, unlike the trade
        # analyzer and `persist_anomalies`. This one proposes a handful of
        # findings across the whole roster -- seven in the last production run --
        # so preloading every stored anomaly would load thousands of rows into
        # the session to save seven queries. Measured: it took the roster pass
        # from 38 statements to 39.

        for member in members:
            anomalies = self.analyze_member(db, member.id, member=member)

            if anomalies:
                members_with_anomalies += 1

                for anomaly in anomalies:
                    # What counts as the same finding lives in one place now:
                    # src/analysis/anomaly_key.py, which the unique indexes
                    # mirror. `seen` is the other half -- the query below cannot
                    # see rows added earlier in this loop and not yet flushed
                    # (SessionLocal is autoflush=False), so without it a batch
                    # holding the same finding twice fails the whole commit.
                    key = identity_of(anomaly)
                    if key is None or key in seen:
                        continue

                    if find_existing(db, key) is not None:
                        # Skip duplicate
                        continue

                    # These two analyzers build Anomaly() directly instead of
                    # going through persist_anomalies(), so the disabled-type
                    # gate has to be repeated here.
                    if anomaly["anomaly_type"] in disabled_types:
                        continue

                    # Store anomaly in database
                    db_anomaly = Anomaly(
                        member_id=anomaly["member_id"],
                        disclosure_id=anomaly.get("disclosure_id"),
                        anomaly_type=anomaly["anomaly_type"],
                        severity=anomaly["severity"],
                        title=anomaly["title"],
                        description=anomaly["description"],
                        computed_value=anomaly.get("computed_value"),
                        threshold_value=anomaly.get("threshold_value"),
                    )
                    db.add(db_anomaly)
                    seen.add(key)

                    all_anomalies.append(
                        {
                            **anomaly,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "state": member.state,
                            "party": member.party.value,
                        }
                    )

            members_analyzed += 1
            if members_analyzed % PROGRESS_EVERY_MEMBERS == 0:
                logger.info(
                    "Wealth analysis: %d/%d members, %d findings so far",
                    members_analyzed,
                    len(members),
                    len(all_anomalies),
                )

        db.commit()

        return {
            "members_analyzed": members_analyzed,
            "members_with_anomalies": members_with_anomalies,
            "total_anomalies": len(all_anomalies),
            "anomalies": all_anomalies,
        }

    def _net_worth_totals(
        self, db: Session, disclosure_ids: Sequence[int]
    ) -> Dict[int, Dict[str, Decimal]]:
        """Asset and liability sums for many filings, in two queries rather than 2n.

        `_calculate_net_worth` issues one SUM over assets and one over
        liabilities for a single filing. Called down a loop over a member's
        filings, down a loop over the roster, that is two network round trips per
        filing against a hosted database -- and this analyzer runs inside the
        ten minutes of silence before the first detector logs anything.

        Grouping by `disclosure_id` asks the same two questions once for the
        whole population. A filing with no assets or no liabilities is simply
        absent from the corresponding result, which is why the caller still
        defaults each side to zero.
        """
        totals: Dict[int, Dict[str, Decimal]] = {}
        if not disclosure_ids:
            return totals

        for row in (
            db.query(
                Asset.disclosure_id,
                func.sum(Asset.value_min),
                func.sum(Asset.value_max),
            )
            .filter(Asset.disclosure_id.in_(disclosure_ids))
            .group_by(Asset.disclosure_id)
            .all()
        ):
            entry = totals.setdefault(row[0], {})
            entry["assets_min"] = row[1] or Decimal(0)
            entry["assets_max"] = row[2] or Decimal(0)

        for row in (
            db.query(
                Liability.disclosure_id,
                func.sum(Liability.amount_min),
                func.sum(Liability.amount_max),
            )
            .filter(Liability.disclosure_id.in_(disclosure_ids))
            .group_by(Liability.disclosure_id)
            .all()
        ):
            entry = totals.setdefault(row[0], {})
            entry["liabilities_min"] = row[1] or Decimal(0)
            entry["liabilities_max"] = row[2] or Decimal(0)

        return totals

    def _calculate_net_worth(
        self,
        db: Session,
        disclosure_id: int,
        totals: Dict[int, Dict[str, Decimal]] | None = None,
    ) -> Dict[str, Decimal | None]:
        """Calculate net worth from a disclosure's assets and liabilities.

        `totals` is the preloaded index from `_net_worth_totals`. Optional so the
        method still works on its own -- it is called that way elsewhere -- but
        every caller walking more than one filing should pass it.
        """
        if totals is not None:
            entry = totals.get(disclosure_id, {})
            assets_min = entry.get("assets_min", Decimal(0))
            assets_max = entry.get("assets_max", Decimal(0))
            liabilities_min = entry.get("liabilities_min", Decimal(0))
            liabilities_max = entry.get("liabilities_max", Decimal(0))
            net_min = assets_min - liabilities_max
            net_max = assets_max - liabilities_min
            # Same shape and the same null rule as the per-filing path below.
            # `mid` is None when either bound is falsy, which is not the same as
            # the midpoint of zero -- a difference the tests hold this to.
            return {
                "min": net_min,
                "max": net_max,
                "mid": (net_min + net_max) / 2 if net_min and net_max else None,
            }

        # Sum assets
        assets_result = (
            db.query(func.sum(Asset.value_min).label("min"), func.sum(Asset.value_max).label("max"))
            .filter(Asset.disclosure_id == disclosure_id)
            .first()
        )

        assets_min = (assets_result.min if assets_result else None) or Decimal(0)
        assets_max = (assets_result.max if assets_result else None) or Decimal(0)

        # Liabilities were previously hardcoded to zero with a comment saying
        # they were "accounted for", which made this gross assets rather than
        # net worth -- inflating every wealth-growth flag derived from it.
        liabilities_result = (
            db.query(
                func.sum(Liability.amount_min).label("min"),
                func.sum(Liability.amount_max).label("max"),
            )
            .filter(Liability.disclosure_id == disclosure_id)
            .first()
        )

        liabilities_min = (liabilities_result.min if liabilities_result else None) or Decimal(0)
        liabilities_max = (liabilities_result.max if liabilities_result else None) or Decimal(0)

        net_min = assets_min - liabilities_max
        net_max = assets_max - liabilities_min

        return {
            "min": net_min,
            "max": net_max,
            "mid": (net_min + net_max) / 2 if net_min and net_max else None,
        }

    def _calculate_severity(self, actual_percent: float, expected_percent: float) -> str:
        """Calculate anomaly severity based on deviation."""
        deviation = actual_percent - expected_percent

        if deviation > 500:
            return "high"
        elif deviation > 200:
            return "medium"
        else:
            return "low"


def analyze_wealth(db: Session, member_id: int | None = None) -> Dict[str, Any]:
    """
    Convenience function to run wealth analysis.

    Args:
        db: Database session
        member_id: Optional member ID (analyzes all if not provided)

    Returns:
        Analysis results
    """
    analyzer = WealthAnalyzer()

    if member_id:
        anomalies = analyzer.analyze_member(db, member_id)
        return {
            "member_id": member_id,
            "anomalies": anomalies,
            "total_anomalies": len(anomalies),
        }
    else:
        return analyzer.analyze_all_members(db)
