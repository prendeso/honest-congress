"""
Advanced Anomaly Detection for Congressional Financial Patterns

Focuses on three key indicators:
1. Net worth compared to salary
2. Stock performance above benchmarks
3. Rapid asset appreciation (e.g., business valuations)
"""

import logging
import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from src.analysis.restatements import member_transactions
from src.db.models import Asset, Disclosure, Liability, Member, Transaction, TransactionType

logger = logging.getLogger(__name__)

# Assets are matched across filings by description, because disclosures carry no
# asset identifier. An exact `description.lower().strip()` match meant any
# wording change between years ("Apple Inc." -> "Apple Inc") read as the old
# asset vanishing and a new one appearing -- inventing appreciation events.
_ASSET_NOISE = re.compile(
    r"\b(inc|inc\.|corp|corporation|co|company|llc|l\.l\.c|ltd|plc|the|common|stock|"
    r"shares|class [a-c]|series [a-c])\b",
    re.IGNORECASE,
)
_ASSET_PUNCT = re.compile(r"[^a-z0-9 ]+")
_ASSET_SPACE = re.compile(r"\s+")


def normalize_asset_key(description: str) -> str:
    """Collapse a disclosed asset description to a stable matching key."""
    text = (description or "").lower()
    text = _ASSET_PUNCT.sub(" ", text)
    text = _ASSET_NOISE.sub(" ", text)
    return _ASSET_SPACE.sub(" ", text).strip()


# Congressional salary by year
CONGRESSIONAL_SALARY_BY_YEAR = {
    2015: 174000,
    2016: 174000,
    2017: 174000,
    2018: 174000,
    2019: 174000,
    2020: 174000,
    2021: 180000,
    2022: 193500,
    2023: 193500,
    2024: 193500,
    2025: 193500,
    2026: 193500,
}


def _fd_disclosures_by_member(db: Session, member_ids: List[int]) -> Dict[int, List[Disclosure]]:
    """Every member's FD filings, oldest first, in one query rather than one each.

    `members_with_annual_filings` already narrows the roster to members with two
    or more of these. What it did not remove was the follow-up query per member
    to go and read them, which against Railway from a GitHub runner is a network
    round trip each.
    """
    # The second half of the same dead filter. `members_with_annual_filings`
    # returned [] because no row carries filing_type "FD"; even once that is
    # fixed, THIS query would still return nothing and the detectors would stay
    # silent -- so both had to move together or neither counted.
    #
    # Ordered the same way as the net-worth series in `wealth_analyzer`, and for
    # the same reason: the caller reads `[0]` and `[-1]` as the first and last
    # filings, and `filing_year` alone does not decide which of a member's six
    # 2024 filings is either one.
    from src.analysis.wealth_analyzer import net_worth_snapshot_clause

    rows = (
        db.query(Disclosure)
        .filter(Disclosure.member_id.in_(member_ids))
        .filter(net_worth_snapshot_clause())
        .order_by(
            Disclosure.member_id,
            Disclosure.filing_year,
            Disclosure.filing_date,
            Disclosure.id,
        )
        .all()
    )
    by_member: Dict[int, List[Disclosure]] = defaultdict(list)
    for disclosure in rows:
        by_member[disclosure.member_id].append(disclosure)
    return by_member


def _assets_by_disclosure(db: Session, disclosure_ids: List[int]) -> Dict[int, List[Asset]]:
    """All the assets at once, keyed by the filing they were reported on.

    Ordered by primary key. Without it the database returns rows in whatever
    order it likes, and `rapid_asset_appreciation` reads that order: two
    holdings a member lists under the same name are compared against each other,
    so WHICH one counts as "before" was decided by the query planner. The same
    defect class as the pagination and wealth-baseline bugs -- an ORDER BY that
    is not total is a result that is not reproducible.
    """
    by_disclosure: Dict[int, List[Asset]] = defaultdict(list)
    if not disclosure_ids:
        return by_disclosure
    rows = (
        db.query(Asset)
        .filter(Asset.disclosure_id.in_(disclosure_ids))
        .order_by(Asset.disclosure_id, Asset.id)
        .all()
    )
    for asset in rows:
        by_disclosure[asset.disclosure_id].append(asset)
    return by_disclosure


def _liabilities_by_disclosure(
    db: Session, disclosure_ids: List[int]
) -> Dict[int, List[Liability]]:
    """The same for liabilities, which net worth is not net worth without."""
    by_disclosure: Dict[int, List[Liability]] = defaultdict(list)
    if not disclosure_ids:
        return by_disclosure
    rows = (
        db.query(Liability)
        .filter(Liability.disclosure_id.in_(disclosure_ids))
        .order_by(Liability.disclosure_id, Liability.id)
        .all()
    )
    for liability in rows:
        by_disclosure[liability.disclosure_id].append(liability)
    return by_disclosure


class AdvancedAnomalyDetector:
    """Detect advanced anomalies in Congressional finances."""

    def __init__(self):
        self.anomalies = []

    def get_salary_for_year(self, year: int) -> int:
        """Get congressional salary for a given year."""
        return CONGRESSIONAL_SALARY_BY_YEAR.get(year, 193500)

    # ========== ANOMALY 1: NET WORTH vs SALARY ==========

    def detect_wealth_vs_salary_anomalies(self, db: Session) -> List[Dict]:
        """
        Detect members whose net worth growth far exceeds what salary could explain.

        Example:
        - Member made $174k/year for 8 years = max $1.392M from salary
        - Member reported net worth grew from $500k to $5M in same period
        - Growth of $4.5M is 3.2x the salary contribution

        Returns: List of anomalies with severity levels
        """
        anomalies = []

        try:
            # Members with multiple years of FD data -- asked for as such,
            # rather than walking all 12,770 and discovering it one query at a
            # time. See `members_with_annual_filings`.
            from src.analysis import members_with_annual_filings

            members = members_with_annual_filings(db)

            # Three queries for the whole detector instead of three per member
            # and per filing. This walked the roster asking for one member's
            # disclosures, then each disclosure's assets, then its liabilities --
            # so a member with five filings cost eleven round trips to Railway.
            # It took 14m39s in the 2026-09-14 cron to return nothing at all.
            by_member = _fd_disclosures_by_member(db, [m.id for m in members])
            every_disclosure = [d.id for ds in by_member.values() for d in ds]
            assets_by_disclosure = _assets_by_disclosure(db, every_disclosure)
            liabilities_by_disclosure = _liabilities_by_disclosure(db, every_disclosure)

            for member in members:
                try:
                    disclosures = by_member.get(member.id, [])

                    if len(disclosures) < 2:
                        continue

                    # Calculate net worth for each year
                    wealth_progression = self._calculate_wealth_progression(
                        db,
                        disclosures,
                        assets_by_disclosure=assets_by_disclosure,
                        liabilities_by_disclosure=liabilities_by_disclosure,
                    )

                    if len(wealth_progression) < 2:
                        continue

                    # Compare to cumulative salary
                    first_year = wealth_progression[0]["year"]
                    last_year = wealth_progression[-1]["year"]
                    years_in_office = last_year - first_year + 1

                    # Calculate cumulative salary they could have saved
                    cumulative_salary = sum(
                        self.get_salary_for_year(year) for year in range(first_year, last_year + 1)
                    )

                    # Get wealth values
                    first_wealth = wealth_progression[0]["net_worth_estimate"]
                    last_wealth = wealth_progression[-1]["net_worth_estimate"]

                    if not first_wealth or not last_wealth:
                        continue

                    wealth_growth = float(last_wealth) - float(first_wealth)

                    # Calculate what portion came from salary vs other sources
                    salary_contribution_ratio = (
                        cumulative_salary / wealth_growth if wealth_growth > 0 else 0
                    )

                    # The claim this publishes -- growth "far exceeds" salary --
                    # was tested against the MIDPOINT of each band and then
                    # stated as fact. `_calculate_wealth_progression` carries the
                    # bounds precisely so a caller need not do that; its own
                    # comment says so ("the bounds are carried alongside so
                    # callers can report them rather than imply precision") and
                    # this caller ignored them.
                    #
                    # The interval is the widest defensible one, so the smallest
                    # increase the filings permit is the later low minus the
                    # earlier high.
                    first_low = float(wealth_progression[0]["net_worth_low"])
                    first_high = float(wealth_progression[0]["net_worth_high"])
                    last_low = float(wealth_progression[-1]["net_worth_low"])
                    last_high = float(wealth_progression[-1]["net_worth_high"])

                    growth_low = last_low - first_high
                    growth_high = last_high - first_low

                    # The old condition -- midpoint growth above salary, and
                    # salary explaining under half of it -- is algebraically
                    # "more than twice cumulative salary, at the midpoint". Held
                    # to the midpoint it is a statement the bands may not
                    # support at all: a member whose interval runs from $200k to
                    # $9M has a midpoint above any threshold you like and
                    # filings entirely consistent with a salary explaining
                    # everything. Requiring the FLOOR of the interval to clear
                    # the same bar makes the published sentence true at every
                    # point the disclosures permit, not just the middle one.
                    #
                    # It is strictly stronger, so it cannot add a finding, and
                    # it will remove those that rested on the width of a band
                    # rather than on the growth. That is the intended effect
                    # (D5): where the bands are too wide to tell, the honest
                    # output is nothing.
                    if growth_low > 2 * cumulative_salary:
                        severity = self._calculate_wealth_severity(
                            growth_low, cumulative_salary, years_in_office
                        )

                        anomalies.append(
                            {
                                "member_id": member.id,
                                "member_name": f"{member.first_name} {member.last_name}",
                                "chamber": member.chamber,
                                "anomaly_type": "wealth_vs_salary",
                                "severity": severity,
                                "years": f"{first_year}-{last_year}",
                                "title": (
                                    f"Wealth growth far exceeds salary ({first_year}-{last_year})"
                                ),
                                "initial_wealth": float(first_wealth),
                                "final_wealth": float(last_wealth),
                                "wealth_growth": wealth_growth,
                                "cumulative_salary": cumulative_salary,
                                "salary_contribution_ratio": salary_contribution_ratio,
                                "growth_multiple_of_salary": wealth_growth / cumulative_salary
                                if cumulative_salary > 0
                                else 0,
                                "net_worth_low_first": first_low,
                                "net_worth_high_first": first_high,
                                "net_worth_low_last": last_low,
                                "net_worth_high_last": last_high,
                                "growth_low": growth_low,
                                "growth_high": growth_high,
                                # A floor, not an estimate. Every figure here is
                                # one end of an interval the filings support; a
                                # midpoint would be a number nobody disclosed.
                                "computed_value": Decimal(str(round(growth_low, 2))),
                                "threshold_value": Decimal(str(round(cumulative_salary, 2))),
                                "description": (
                                    f"Reported net worth went from a range of "
                                    f"${first_low:,.0f}-${first_high:,.0f} in {first_year} to "
                                    f"${last_low:,.0f}-${last_high:,.0f} in {last_year}. "
                                    f"On the least favourable reading of those bands the increase "
                                    f"is still at least ${growth_low:,.0f}, more than "
                                    f"{growth_low / cumulative_salary:.1f}x the ${cumulative_salary:,.0f} "
                                    f"of congressional salary over {years_in_office} years. "
                                    f"Disclosures report bands and never exact figures, so no point "
                                    f"figure for any of these exists."
                                ),
                            }
                        )

                except Exception as e:
                    logger.debug(
                        f"Error analyzing {member.first_name} {member.last_name}: {str(e)[:50]}"
                    )

        except Exception as e:
            logger.error(f"Error in wealth vs salary detection: {str(e)[:100]}")

        return anomalies

    def _calculate_wealth_progression(
        self,
        db: Session,
        disclosures: List[Disclosure],
        *,
        assets_by_disclosure: Dict[int, List[Asset]] | None = None,
        liabilities_by_disclosure: Dict[int, List[Liability]] | None = None,
    ) -> List[Dict]:
        """Calculate estimated net worth for each year's disclosure.

        The two index arguments are optional so the method still works on its
        own, which the tests rely on. When a caller is walking a whole roster it
        passes them in, and the two queries per filing below become none.
        """
        progression = []

        for disclosure in disclosures:
            if assets_by_disclosure is not None:
                assets = assets_by_disclosure.get(disclosure.id, [])
            else:
                assets = db.query(Asset).filter(Asset.disclosure_id == disclosure.id).all()

            # Midpoint of each reported band. Disclosures report ranges, so this
            # is an estimate with real error bars -- the bounds are carried
            # alongside so callers can report them rather than imply precision.
            total_value = Decimal(0)
            lower_bound = Decimal(0)
            upper_bound = Decimal(0)
            for asset in assets:
                if asset.value_min and asset.value_max:
                    total_value += (asset.value_min + asset.value_max) / 2
                    lower_bound += asset.value_min
                    upper_bound += asset.value_max
                elif asset.value_max:
                    total_value += asset.value_max
                    upper_bound += asset.value_max

            # Liabilities were ignored entirely, which made this gross assets
            # rather than net worth. Subtract the opposite bound of each range
            # so the interval stays honest.
            if liabilities_by_disclosure is not None:
                liabilities = liabilities_by_disclosure.get(disclosure.id, [])
            else:
                liabilities = (
                    db.query(Liability).filter(Liability.disclosure_id == disclosure.id).all()
                )
            liab_min = Decimal(0)
            liab_max = Decimal(0)
            for liability in liabilities:
                if liability.amount_min and liability.amount_max:
                    total_value -= (liability.amount_min + liability.amount_max) / 2
                    liab_min += liability.amount_min
                    liab_max += liability.amount_max
                elif liability.amount_max:
                    total_value -= liability.amount_max
                    liab_max += liability.amount_max

            progression.append(
                {
                    "year": disclosure.filing_year,
                    "disclosure_id": disclosure.id,
                    "net_worth_estimate": total_value if total_value > 0 else None,
                    # Widest defensible interval: lowest assets minus highest
                    # debts, and vice versa.
                    "net_worth_low": lower_bound - liab_max,
                    "net_worth_high": upper_bound - liab_min,
                    "asset_count": len(assets),
                }
            )

        return progression

    def _calculate_wealth_severity(self, wealth_growth: float, salary: float, years: int) -> str:
        """Calculate severity level for wealth anomaly."""
        ratio = wealth_growth / salary if salary > 0 else 0

        if ratio > 5:
            return "CRITICAL"
        elif ratio > 3:
            return "HIGH"
        elif ratio > 1.5:
            return "MEDIUM"
        else:
            return "LOW"

    # ========== ANOMALY 2: ASSET APPRECIATION ==========

    def detect_asset_appreciation_anomalies(self, db: Session) -> List[Dict]:
        """
        Detect assets that appreciated dramatically in short time periods.

        Example: Ilhan Omar's winery business
        - 2021: $15,000
        - 2022: $1,000,000+
        - Growth: 6,567% in one year (highly suspicious)

        Returns: List of anomalies with timeline and growth metrics
        """
        anomalies = []

        try:
            # Same precondition as the wealth loop above: two FD filings to
            # compare. Same scoping, for the same reason.
            from src.analysis import members_with_annual_filings

            members = members_with_annual_filings(db)

            # Same preload as the wealth loop, for the same reason: this one
            # took 14m40s in the same run, also to return nothing.
            by_member = _fd_disclosures_by_member(db, [m.id for m in members])
            assets_by_disclosure = _assets_by_disclosure(
                db, [d.id for ds in by_member.values() for d in ds]
            )

            for member in members:
                try:
                    disclosures = by_member.get(member.id, [])

                    if len(disclosures) < 2:
                        continue

                    # Track assets year-over-year. Annotated because the
                    # entries are heterogeneous -- without it mypy infers
                    # Dict[str, object] and every numeric read below fails.
                    assets_by_description: defaultdict[str, List[Dict[str, Any]]] = defaultdict(
                        list
                    )

                    # One entry per (asset, YEAR), not per row. A member who
                    # holds the same thing in two accounts lists it twice in one
                    # filing -- "Fidelity Inv. - IRA Cash [IH]" appears twice in
                    # Warren Davidson's 2024 annual -- and the loop below
                    # compares CONSECUTIVE entries. Two rows from the same filing
                    # were therefore compared against each other, as though one
                    # had grown into the other over no time at all: `years_diff`
                    # is 0, `annual_growth` falls back to the raw growth, and
                    # `annual_growth > 500` still fires. A member listing the
                    # same asset at $1-$1,000 in one account and $500,001-$1M in
                    # another produces "49,900% appreciation (2024-2024)",
                    # CRITICAL, under their name.
                    #
                    # Summing is the right arithmetic as well as the safe one:
                    # what the member holds of that asset in that year is the
                    # total across the accounts they hold it in.
                    per_year: defaultdict[str, Dict[int, Dict[str, Any]]] = defaultdict(dict)

                    for disclosure in disclosures:
                        assets = assets_by_disclosure.get(disclosure.id, [])

                        for asset in assets:
                            key = normalize_asset_key(asset.description)

                            if asset.value_max:
                                value_min = float(asset.value_min or 0)
                                value_max = float(asset.value_max)
                                year = disclosure.filing_year
                                held = per_year[key].get(year)
                                if held is None:
                                    per_year[key][year] = {
                                        "year": year,
                                        "value_min": value_min,
                                        "value_max": value_max,
                                        "asset_type": asset.asset_type,
                                        "description": asset.description,
                                    }
                                else:
                                    held["value_min"] += value_min
                                    held["value_max"] += value_max

                    for key, by_year in per_year.items():
                        for entry in by_year.values():
                            # Midpoint for display; the bounds drive the actual
                            # decision below.
                            entry["value"] = (entry["value_min"] + entry["value_max"]) / 2
                            assets_by_description[key].append(entry)

                    # Check for suspicious appreciation
                    for asset_desc, values in assets_by_description.items():
                        if len(values) < 2:
                            continue

                        values_sorted = sorted(values, key=lambda x: x["year"])

                        # Check each year-over-year change
                        for i in range(1, len(values_sorted)):
                            prev = values_sorted[i - 1]
                            curr = values_sorted[i]

                            years_diff = curr["year"] - prev["year"]
                            prev_value = prev["value"]
                            curr_value = curr["value"]
                            prev_max = prev["value_max"]
                            curr_min = curr["value_min"]
                            value_growth = curr_value - prev_value

                            if prev_value > 0:
                                # Disclosures report bands, so the smallest
                                # growth consistent with both filings is
                                # (this year's floor) vs (last year's ceiling).
                                # Comparing band edges -- as this did, using
                                # value_max on both sides -- turns a $1 move
                                # across a bracket boundary into "233% growth".
                                # Only flag growth the bands actually guarantee.
                                guaranteed_growth_percent = (
                                    (curr_min - prev_max) / prev_max * 100 if prev_max > 0 else 0.0
                                )
                                growth_percent = (value_growth / prev_value) * 100
                                annual_growth = (
                                    guaranteed_growth_percent / years_diff
                                    if years_diff > 0
                                    else guaranteed_growth_percent
                                )

                                if annual_growth > 500 or (
                                    years_diff == 1 and guaranteed_growth_percent > 100
                                ):
                                    severity = (
                                        "CRITICAL" if guaranteed_growth_percent > 1000 else "HIGH"
                                    )

                                    anomalies.append(
                                        {
                                            "member_id": member.id,
                                            "member_name": f"{member.first_name} {member.last_name}",
                                            "chamber": member.chamber,
                                            "anomaly_type": "rapid_asset_appreciation",
                                            "severity": severity,
                                            "title": (
                                                f"Rapid appreciation: {asset_desc[:60]} "
                                                f"({prev['year']}-{curr['year']})"
                                            ),
                                            "asset_description": asset_desc,
                                            "asset_type": curr["asset_type"],
                                            "start_year": prev["year"],
                                            "end_year": curr["year"],
                                            "start_value": prev["value"],
                                            "end_value": curr["value"],
                                            "growth_amount": value_growth,
                                            "growth_percent": growth_percent,
                                            "annual_growth_rate": annual_growth,
                                            "guaranteed_growth_percent": (
                                                guaranteed_growth_percent
                                            ),
                                            "computed_value": Decimal(
                                                str(round(guaranteed_growth_percent, 2))
                                            ),
                                            "threshold_value": Decimal("100"),
                                            "description": (
                                                f"{asset_desc} rose from the "
                                                f"${prev['value_min']:,.0f}-${prev['value_max']:,.0f} band "
                                                f"({prev['year']}) to the "
                                                f"${curr['value_min']:,.0f}-${curr['value_max']:,.0f} band "
                                                f"({curr['year']}). Growth is at least "
                                                f"{guaranteed_growth_percent:.0f}% over {years_diff} year(s) "
                                                f"({annual_growth:.0f}% annually); disclosures report "
                                                f"ranges, so the exact figure is not knowable."
                                            ),
                                        }
                                    )

                except Exception as e:
                    logger.debug(f"Error analyzing assets for {member.first_name}: {str(e)[:50]}")

        except Exception as e:
            logger.error(f"Error in asset appreciation detection: {str(e)[:100]}")

        return anomalies

    # ========== ANOMALY 3: STOCK PERFORMANCE vs BENCHMARKS ==========

    def detect_stock_outperformance_anomalies(
        self, db: Session, benchmark_annual_return: float = 0.10
    ) -> List[Dict]:
        """
        Detect Congressional trading that outperforms market benchmarks.

        Benchmark: S&P 500 annual return (default 10%)
        Metric: Trading profits vs benchmark

        Example:
        - S&P 500 returned 10% in 2022
        - Member's trades returned 45% in same period
        - Benchmarked against a hardcoded constant, not real index returns;
          disabled by default (see docs/DECISIONS.md).

        Returns: List of members with suspicious trading performance
        """
        anomalies = []

        try:
            members = db.query(Member).all()

            for member in members:
                try:
                    # Get all trades for this member (joined through Disclosure
                    # because Transaction has no direct member_id column).
                    trades = member_transactions(db, member.id)

                    if not trades:
                        continue

                    # Group trades by year
                    trades_by_year = defaultdict(list)
                    for trade in trades:
                        year = trade.transaction_date.year
                        trades_by_year[year].append(trade)

                    # Analyze each year
                    for year, year_trades in trades_by_year.items():
                        year_anomaly = self._analyze_year_trading_performance(
                            member, year, year_trades, benchmark_annual_return
                        )

                        if year_anomaly:
                            anomalies.append(year_anomaly)

                except Exception as e:
                    logger.debug(f"Error analyzing trades for {member.first_name}: {str(e)[:50]}")

        except Exception as e:
            logger.error(f"Error in stock performance detection: {str(e)[:100]}")

        return anomalies

    def _analyze_year_trading_performance(
        self, member: Member, year: int, trades: List[Transaction], benchmark: float
    ) -> Dict | None:
        """Analyze trading performance for a specific year."""

        if not trades:
            return None

        # Calculate estimated returns
        # Buy trades = negative (outflow), Sell trades = positive (inflow)
        from src.analysis import transaction_amount

        buys = [t for t in trades if t.transaction_type == TransactionType.PURCHASE]
        sells = [t for t in trades if t.transaction_type == TransactionType.SALE]

        if not buys or not sells:
            return None

        total_bought = Decimal(str(sum(transaction_amount(t) for t in buys)))
        total_sold = Decimal(str(sum(transaction_amount(t) for t in sells)))

        if total_bought == 0:
            return None

        # Calculate simple return: (proceeds - costs) / costs
        gross_profit = total_sold - total_bought
        return_percent = float((gross_profit / total_bought) * 100) if total_bought > 0 else 0

        # Compare to benchmark
        excess_return = return_percent - (benchmark * 100)

        # Flag if return is 2x+ the benchmark (20% vs 10% expected)
        if excess_return > (benchmark * 100):
            severity = "CRITICAL" if excess_return > (benchmark * 200) else "HIGH"

            return {
                "member_id": member.id,
                "member_name": f"{member.first_name} {member.last_name}",
                "chamber": member.chamber,
                "anomaly_type": "outperforming_trades",
                "severity": severity,
                "title": f"Trading returns outperformed market benchmark ({year})",
                "year": year,
                "trades_count": len(trades),
                "buy_trades": len(buys),
                "sell_trades": len(sells),
                "total_invested": float(total_bought),
                "total_proceeds": float(total_sold),
                "gross_profit": float(gross_profit),
                "return_percent": return_percent,
                "benchmark_return": benchmark * 100,
                "excess_return": excess_return,
                "computed_value": Decimal(str(round(return_percent, 2))),
                "threshold_value": Decimal(str(round(benchmark * 100, 2))),
                "description": (
                    f"{member.first_name} {member.last_name} trading in {year}: "
                    f"Invested ${total_bought:,.0f}, received ${total_sold:,.0f}. "
                    f"Return: {return_percent:.1f}% vs benchmark {benchmark * 100:.1f}%. "
                    f"Excess return: {excess_return:.1f}% ({excess_return / (benchmark * 100):.1f}x benchmark). "
                    f"{len(buys)} buys, {len(sells)} sells."
                ),
            }

        return None


def run_advanced_anomaly_detection(db: Session, persist: bool = True) -> Dict:
    """Run all three advanced anomaly detection types.

    When `persist` is true, detected anomalies are written to the database
    via `persist_anomalies` (deduplicated by member_id+type+title).
    """
    from src.analysis import detector_is_disabled, persist_anomalies

    detector = AdvancedAnomalyDetector()

    logger.info("\n" + "=" * 70)
    logger.info("ADVANCED ANOMALY DETECTION")
    logger.info("=" * 70 + "\n")

    logger.info("1. Detecting wealth vs salary anomalies...")
    wealth_anomalies = detector.detect_wealth_vs_salary_anomalies(db)
    logger.info(f"   Found {len(wealth_anomalies)} anomalies\n")

    logger.info("2. Detecting rapid asset appreciation...")
    asset_anomalies = detector.detect_asset_appreciation_anomalies(db)
    logger.info(f"   Found {len(asset_anomalies)} anomalies\n")

    # Not run when disabled, rather than run and discarded. `outperforming_trades`
    # benchmarks against a hardcoded flat 10% and computes "return" as
    # (sells - buys)/buys with no position matching, which is why it is
    # disabled -- and it cost 17m15s of a production analysis step to produce
    # 23 findings that `persist_anomalies` then dropped on the floor.
    if detector_is_disabled("outperforming_trades"):
        logger.info("3. Stock outperformance is disabled; not running it.\n")
        stock_anomalies: List[Dict] = []
    else:
        logger.info("3. Detecting stock outperformance...")
        stock_anomalies = detector.detect_stock_outperformance_anomalies(db)
        logger.info(f"   Found {len(stock_anomalies)} anomalies\n")

    persisted = 0
    if persist:
        persisted += persist_anomalies(db, wealth_anomalies)
        persisted += persist_anomalies(db, asset_anomalies)
        persisted += persist_anomalies(db, stock_anomalies)
        logger.info(f"Persisted {persisted} new anomalies to database.\n")

    total = len(wealth_anomalies) + len(asset_anomalies) + len(stock_anomalies)
    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Total anomalies detected: {total}")
    logger.info(f"  • Wealth/Salary: {len(wealth_anomalies)}")
    logger.info(f"  • Asset Appreciation: {len(asset_anomalies)}")
    logger.info(f"  • Stock Outperformance: {len(stock_anomalies)}")
    logger.info("=" * 70 + "\n")

    return {
        "wealth_anomalies": wealth_anomalies,
        "asset_anomalies": asset_anomalies,
        "stock_anomalies": stock_anomalies,
        "persisted": persisted,
        "total": total,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    from src.db.database import SessionLocal

    db = SessionLocal()
    results = run_advanced_anomaly_detection(db)
    db.close()
