"""Trade anomaly analyzer for congressional stock transactions."""

import logging
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Sequence

from sqlalchemy.orm import Session

from src.analysis.anomaly_key import identity_of, stored_by_identity
from src.analysis.asset_class import all_fixed_income, is_option
from src.analysis.attribution import (
    excluded_clause,
    held_by_member,
    owner_of,
    trades_the_member_holds,
)
from src.analysis.restatements import drop_restated_pairs, member_transactions
from src.analysis.sectors import SectorIndex
from src.config import get_settings
from src.db.models import Anomaly, Disclosure, Member, Transaction, TransactionType
from src.parsing.ptr_parser import AMENDED, ASSET_CLASS_TAG

logger = logging.getLogger(__name__)

# How often each analyzer says where it has got to. Both of these ran in
# complete silence: the gap between `analyze` starting and the first detector
# logging anything was 10m42s in the production run of 2026-09-14, and nothing
# in the log said which of the two it was, or whether either was moving.
PROGRESS_EVERY_MEMBERS = 100

_settings = get_settings()

# Sector keywords for classification
# Sector classification lives in one place: src/analysis/sectors.py. This module
# used to carry its own keyword table -- a fifth copy, with sectors "retail" and
# "real_estate" that exist nowhere else -- and matched it as an unanchored
# substring against description + ticker concatenated together, so "gas" hit
# "Las Vegas Sands". It also carried a COMMITTEE_SECTORS map with no callers at
# all. Both are gone; `SectorIndex` classifies on the issuer's own SEC industry
# code, which covers every registrant rather than a remembered handful.


def _excluded_clause(household_rows) -> str:
    """The month's scope. The clause itself lives in `attribution`."""
    return excluded_clause(household_rows or [], "The filings covering that month also report")


def _corporate_actions(rows) -> tuple:
    """Split a month's rows into trades and the exchanges that are not trades.

    The House form's transaction type is `P`, `S` or `E`, and `E` is not a
    third direction -- it is a holding converted in kind. All 46 exchange rows
    in the corpus are corporate actions and not one is a discretionary trade:
    Exxon/Pioneer, Jacobs/Amentum, Liberty Media/SiriusXM, Synopsys/Ansys,
    Capital One/Discover, Chevron/Hess, the Sandisk and Qnity and Solstice
    spin-offs, and a run of municipal refundings. Several print the reason on
    the row -- "Holdings in J exchanged out for receipt of new holdings in J
    and AMTM through a corporate action."

    A detector whose subject is how often a member traded must not count them.
    Rep. Kean was published as "High trading activity: 15 trades in September
    2024". He made one. The other fourteen were Jacobs Solutions becoming
    Amentum.

    They are named rather than dropped silently, the same rule `_excluded_clause`
    applies to a spouse's rows, so a reader checking against the PDF still gets
    back to the printed total.
    """
    traded = [t for t in rows if t.transaction_type != TransactionType.EXCHANGE]
    exchanged = [t for t in rows if t.transaction_type == TransactionType.EXCHANGE]
    return traded, exchanged


def _exchange_clause(exchanged) -> str:
    if not exchanged:
        return ""
    n = len(exchanged)
    return (
        f" The filings covering that month also report {n} exchange(s) -- "
        f"holdings converted in kind, which the form marks `E` and which this "
        f"count excludes because they are not trades the member placed."
    )


ASSET_NAME_LIMIT = 60


def _asset_name(txn, limit: int = ASSET_NAME_LIMIT) -> str:
    """What was traded, named so the name is readable and reconcilable.

    Two blind slices published unreadable text about named people. `late_filing`
    took `description[:30]` and `large_trade` took `[:20]`, both cutting
    wherever the character fell:

        Trade: purchase Cleveland-Cliffs Inc. Common S
        Trade: sale American Funds Income Fund of\n
        Large transaction: US Treasury Bill [GS purchase (more than $1,000,000)
        Large transaction: Garden of Eden LLC,  sale (more than $1,000,000)

    72 of the corpus's 174 late-filing findings truncate mid-word and 11 carry
    an embedded newline into the published sentence; 59 of 80 large-trade
    titles do, and three different Treasury bills produce the SAME title --
    saved from colliding only because that finding is keyed on
    `transaction_id`.

    So: the ticker when the filing gave one; otherwise the description with its
    whitespace collapsed (the weak text path leaves newlines in it) and its
    asset-class tag removed, cut at a word boundary. `[XX]` goes because it is
    the form's asset-type code, not part of the name -- D21 -- and "US Treasury
    Bill [GS" is the shape that makes the case.
    """
    if getattr(txn, "ticker", None):
        return txn.ticker
    text = ASSET_CLASS_TAG.sub(" ", getattr(txn, "description", None) or "")
    text = " ".join(text.split())
    if not text:
        return "unknown"
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut or text[:limit]}..."


def _whose_trade(txn) -> str:
    """Name the owner when the filing says the row is not the member's own.

    93 of 174 late-filing findings sit on a row the form attributes to somebody
    else -- 91 a spouse, 2 a dependent child -- under a sentence naming the
    member and nothing else. The rows are scored on purpose and must stay
    scored: the STOCK Act duty is the MEMBER's for the whole household, which
    is why `attribution` explicitly exempts this detector. What was missing is
    the fact, not the finding.
    """
    owner = owner_of(txn)
    words = {
        "Spouse": "their spouse",
        "Dependent Child": "a dependent child",
    }
    whose = words.get(owner)
    if whose is None:
        return ""
    return (
        f", which the filing reports for {whose}. The STOCK Act deadline is the "
        f"member's for every transaction their household must report, so this "
        f"row is scored against them"
    )


def _over_how_many_days(rows) -> str:
    """How concentrated the month was, because "activity" implies spread.

    19 of the corpus's 132 "High trading activity" findings describe a month
    whose every row shares ONE date -- Rep. Keating's fifteen are all 11
    September 2024, Sen. Tuberville's sixteen all 15 April 2025. That is one
    reallocation, and calling it a month of trading activity without saying so
    is the same defect #103 fixed in `trade_clustering`: a PTR records a date
    and no time of day, so a single date is a batch, not a sequence and not a
    month's worth of decisions.

    The multi-day form stays to one short sentence, because it is the common
    case and the count already carries the claim.
    """
    days = sorted({t.transaction_date.date() for t in rows if t.transaction_date})
    if not days:
        return ""
    if len(days) == 1:
        return (
            f" All of them are dated {days[0]:%-d %B %Y}; a PTR records a date but no "
            f"time of day, so this is one day's batch rather than trading spread "
            f"through the month."
        )
    return f" They fall on {len(days)} days of trading."


def _percent(value: float) -> str:
    """The exact share, not a band it happens to fall in.

    `computed_value` already carries this number and the card renders it two
    lines below the title, so publishing "60-75%" beside "60 percent" hid a
    fact the filing states exactly. One decimal only where there is one.
    """
    return f"{value:.0f}%" if abs(value - round(value)) < 0.05 else f"{value:.1f}%"


def _excluded_from_filing(household_rows) -> str:
    """The same clause for a scope of one filing rather than one month."""
    return excluded_clause(household_rows or [], "The same filing also reports")


def _what_was_traded(transactions: Sequence[Transaction]) -> str:
    """The noun phrase for a month's rows, asserting only what the form says.

    Reads as "18 transactions were" or "18 government or municipal securities
    were", so the caller's sentence works either way.
    """
    if all_fixed_income(transactions):
        return "government or municipal securities were"
    return "transactions were"


class TradeAnalyzer:
    """
    Analyzes congressional stock trades for suspicious patterns.

    Detects:
    - Trades clustered before significant legislation/events
    - Unusual sector concentration
    - High trading frequency
    - Late PTR filings (>45 days)
    - Potential committee conflicts
    """

    def __init__(
        self,
        ptr_deadline_days: int = 45,
        min_trades_for_concentration: int = 5,
        concentration_threshold_percent: float = 50.0,
        frequency_threshold_per_month: int = 10,
        late_filing_min_days: int | None = None,
        late_filing_min_amount_usd: int | None = None,
    ):
        self.ptr_deadline_days = ptr_deadline_days
        self.min_trades_for_concentration = min_trades_for_concentration
        # Loaded lazily on first use, then reused across every member.
        self._index: SectorIndex | None = None
        # Set once `analyze_all_members` has reconciled every large trade in a
        # single pass, so the per-member call does not repeat it. Per analyzer
        # instance, like `_index`, because that is the scope of one run.
        self._large_trades_synced = False
        self.concentration_threshold_percent = concentration_threshold_percent
        self.frequency_threshold_per_month = frequency_threshold_per_month
        # The "late filing" detector previously fired on every PTR more than
        # 45 days late, generating thousands of low-signal anomalies. We now
        # require a much later filing AND a non-trivial transaction size.
        self.late_filing_min_days = (
            late_filing_min_days
            if late_filing_min_days is not None
            else _settings.late_filing_min_days
        )
        self.late_filing_min_amount_usd = (
            late_filing_min_amount_usd
            if late_filing_min_amount_usd is not None
            else _settings.late_filing_min_amount_usd
        )

    def _large_trade_asset_name(self, txn: Transaction) -> str:
        return _asset_name(txn)

    def _build_large_trade_text(self, txn: Transaction) -> Dict[str, str]:
        """What was bought, named as the form names it.

        The asset name on an option row is the UNDERLYING. "Microsoft
        Corporation - Common Stock (MSFT) [OP]" is a Microsoft call, not
        Microsoft stock, and the form says which on its own Description line:
        "D: Call options; Strike price $240; Expires 9/19/2025". 13 of the
        corpus's 80 large-trade findings sit on such a row and read "A purchase
        of MSFT worth more than $1,000,000 was reported" -- a stock purchase
        that did not happen.

        The amount needs saying too. On an option row the disclosed band is the
        transaction's own value, not the value of the shares it controls, and
        those differ by roughly the leverage. Publishing "more than $1,000,000"
        beside a company name invites the second reading.
        """
        asset_name = self._large_trade_asset_name(txn)
        noun = f"{asset_name} options" if is_option(txn) else asset_name
        title = f"Large transaction: {noun} {txn.transaction_type.value} (more than $1,000,000)"
        description = (
            f"A {txn.transaction_type.value} of {noun} worth more than $1,000,000 was reported."
        )
        if is_option(txn):
            description += (
                " The form marks this row `[OP]`, so the asset named is the "
                "underlying and the trade is in options on it. The amount is the "
                "value the filing reports for the transaction, not the value of "
                "the underlying shares."
            )
        description += " Large transactions warrant additional scrutiny."
        return {"title": title, "description": description}

    def _sync_large_trade_anomalies(self, db: Session, member_id: int | None = None) -> None:
        """Backfill transaction_id and sync title/description for large trades.

        Three round trips per large trade, and it ran over every one of them
        twice. `analyze_all_members` calls this once unfiltered and then
        `analyze_member` calls it again for each member, so on the nightly path
        every large trade was reconciled, then reconciled again.

        The queries are now three for the whole call rather than three per row:

        * the driving query already joins `Disclosure`, so it selects it instead
          of fetching the same row back one at a time;
        * the `large_trade` findings are read once and indexed twice, by
          `transaction_id` and by the `(member, disclosure, title)` fallback the
          backfill needs, rather than queried per trade.
        """
        large_trade_threshold = Decimal("1000000")
        query = (
            db.query(Transaction, Disclosure)
            .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
            .filter(Transaction.amount_min > large_trade_threshold)
        )
        if member_id:
            query = query.filter(Disclosure.member_id == member_id)

        # Restated rows dropped. A large trade refiled by an amendment gets a
        # second `transaction_id`, and `identity_of` keys trade findings on
        # exactly that, so the duplicate finding is NOT collapsed downstream --
        # the same $1m+ trade would be published twice under one name.
        # Same rule as the finding this reconciles: a $1m+ purchase the
        # filing marks `SP` is the spouse's, and syncing a title for it
        # would re-assert an attribution `_check_large_trades` no longer
        # makes.
        rows = [row for row in drop_restated_pairs(query.all()) if held_by_member(row[0])]
        if not rows:
            return

        # Both indexes come from one read. The second exists because a finding
        # written before `transaction_id` was populated can only be recognised
        # by its title, which is exactly what this function is here to repair.
        findings = db.query(Anomaly).filter(Anomaly.anomaly_type == "large_trade").all()
        by_transaction = {a.transaction_id: a for a in findings if a.transaction_id is not None}
        by_title = {
            (a.member_id, a.disclosure_id, a.title): a for a in findings if a.transaction_id is None
        }

        for txn, disclosure in rows:
            text = self._build_large_trade_text(txn)

            existing = by_transaction.get(txn.id)
            if existing:
                if (
                    existing.title != text["title"]
                    or existing.description != text["description"]
                    or existing.disclosure_id != disclosure.id
                ):
                    existing.title = text["title"]
                    existing.description = text["description"]
                    existing.disclosure_id = disclosure.id
                continue

            existing_no_txn = by_title.get((disclosure.member_id, disclosure.id, text["title"]))

            if existing_no_txn:
                existing_no_txn.transaction_id = txn.id
                if existing_no_txn.description != text["description"]:
                    existing_no_txn.description = text["description"]
                # It has an id now, so a later row in this same pass finds it
                # where it will look rather than matching the title again.
                by_transaction[txn.id] = existing_no_txn

    def analyze_member(
        self, db: Session, member_id: int, member: Member | None = None
    ) -> List[Dict[str, Any]]:
        """
        Analyze a single member for trade anomalies.

        Args:
            db: Database session
            member_id: Member ID to analyze
            member: the already-loaded row, when the caller has it

        Returns:
            List of detected anomalies
        """
        # `analyze_all_members` is holding this row already, having just
        # selected it; fetching it back one member at a time is a round trip per
        # member to learn what the caller could have said. Still optional, so the
        # single-member entry point keeps working unchanged.
        if member is None:
            member = db.query(Member).filter(Member.id == member_id).first()
        if not member:
            return []

        anomalies = []

        # Two lists, and the difference between them is the point.
        #
        # `disclosed` is everything the member had to report -- their spouse's
        # and dependent children's trades included. It gates the early return
        # and it is what `_check_late_filings` scores, because the STOCK Act
        # deadline is the MEMBER's duty for the whole household.
        #
        # `transactions` is the subset the member is a party to. Sector
        # concentration, trading frequency and large trades all publish a
        # sentence of the form "this member did X", so they may only count
        # rows the filing attributes to them.
        #
        # Collapsing these into one filtered list silently erased 85 real late
        # filings from a 596-finding corpus -- every member whose disclosed
        # rows were all their spouse's returned at the guard above before
        # `_check_late_filings` ever ran.
        disclosed = member_transactions(db, member_id)
        transactions = trades_the_member_holds(disclosed)

        if not disclosed:
            return []

        # Skipped when `analyze_all_members` has already reconciled every large
        # trade in one pass. Not deleted: `analyze_member` is also the public
        # `--member-id` entry point, and there it is the only pass there is.
        if not self._large_trades_synced:
            self._sync_large_trade_anomalies(db, member_id=member_id)

        anomalies.extend(self._check_late_filings(db, member_id, member))
        anomalies.extend(
            self._check_sector_concentration(
                transactions, member_id, member, self._sector_index(db), disclosed
            )
        )
        anomalies.extend(self._check_trading_frequency(transactions, member_id, member, disclosed))
        anomalies.extend(self._check_large_trades(transactions, member_id, member))

        return anomalies

    def _check_late_filings(
        self, db: Session, member_id: int, member: Member
    ) -> List[Dict[str, Any]]:
        """Check for materially late PTR filings.

        Only flags trades where (filing_date - transaction_date) exceeds
        `late_filing_min_days` AND the transaction's max amount meets
        `late_filing_min_amount_usd`. These thresholds keep the signal
        meaningful — the raw "anything past the 45-day STOCK Act deadline"
        check produced thousands of small, noisy anomalies.
        """
        anomalies = []
        min_amount = Decimal(str(self.late_filing_min_amount_usd))

        # One query, not one per filing. This read the member's PTRs and then
        # asked for each one's transactions in turn, so a member with forty
        # filings was forty-one round trips -- and PTRs are the most numerous
        # filing type there is.
        #
        # The join is equivalent rather than merely similar: a filing with no
        # transactions contributed nothing to the loop before, and does not
        # appear in the join now.
        rows = (
            db.query(Transaction, Disclosure)
            .join(Disclosure, Transaction.disclosure_id == Disclosure.id)
            .filter(
                Disclosure.member_id == member_id,
                Disclosure.is_ptr == True,
                Disclosure.parsed == True,
            )
            .all()
        )

        # Restated rows dropped, keeping the EARLIEST filing that reported each
        # trade. That is the filing the STOCK Act's 45-day clock runs against,
        # so a trade refiled later by an amendment is still scored against the
        # report that first disclosed it rather than being re-accused of
        # lateness. `catalog.py` warns of this in prose; this is the guard.
        rows = drop_restated_pairs(rows)

        for txn, disclosure in rows:
            if not (txn.transaction_date and disclosure.filing_date):
                continue

            # The form's own answer, and it outranks the content match above.
            # A row the filer marked "Amended" restates one already disclosed,
            # so the 45-day clock belongs to the filing that first reported the
            # trade, not to this one.
            #
            # `drop_restated_pairs` catches this when the two rows agree on
            # content, and they need not: Rep. Laurel Lee's re-filing writes
            # "2000114315 SP Alibaba Group Holding Limited" where the original
            # wrote "SP Alibaba Group Holding Limited S (partial)", so the keys
            # miss and a trade reported six days after it happened was
            # published as 591 days late.
            #
            # When the original is in the corpus it is scored on its own merits,
            # timely or not. When it is not, this stays silent rather than
            # accuse -- a missed late filing is the cheaper error, and the only
            # one that is not about a named person.
            if txn.filing_status == AMENDED:
                continue

            days_to_file = (disclosure.filing_date - txn.transaction_date).days
            if days_to_file <= self.late_filing_min_days:
                continue

            # Skip small trades — late filings on de minimis amounts are
            # mostly clerical and we don't want to flood the table.
            txn_amount = txn.amount_max or txn.amount_min
            if txn_amount is None or txn_amount < min_amount:
                continue

            days_late = days_to_file - self.ptr_deadline_days
            if days_late <= 30:
                severity = "low"
                late_range = "moderately late (1-4 weeks)"
            elif days_late <= 90:
                severity = "medium"
                late_range = "significantly late (1-3 months)"
            else:
                severity = "high"
                late_range = "severely late (over 3 months)"

            anomalies.append(
                {
                    "member_id": member_id,
                    "disclosure_id": disclosure.id,
                    "transaction_id": txn.id,
                    "anomaly_type": "late_filing",
                    "severity": severity,
                    "title": f"Late PTR filing: {late_range}",
                    "description": (
                        f"Transaction on {txn.transaction_date.strftime('%Y-%m-%d')} "
                        f"was filed {late_range} on "
                        f"{disclosure.filing_date.strftime('%Y-%m-%d')}. "
                        f"The STOCK Act requires filing within {self.ptr_deadline_days} days. "
                        f"Trade reported: {txn.transaction_type.value} of "
                        f"{_asset_name(txn)}{_whose_trade(txn)}."
                    ),
                    "computed_value": Decimal(str(days_to_file)),
                    "threshold_value": Decimal(str(self.ptr_deadline_days)),
                }
            )

        return anomalies

    def _sector_index(self, db: Session) -> SectorIndex:
        """Load the industry codes once per analyzer, not once per member.

        `analyze_all_members` walks every member in turn, and rebuilding this
        for each of ~550 of them would turn one query into 550.
        """
        if self._index is None:
            self._index = SectorIndex.from_db(db)
        return self._index

    def _check_sector_concentration(
        self,
        transactions: List[Transaction],
        member_id: int,
        member: Member,
        index: SectorIndex,
        disclosed: List[Transaction] | None = None,
    ) -> List[Dict[str, Any]]:
        """How much of ONE filing was in one sector, said so it can be checked.

        The number was right and the sentence was not, the same pairing #104
        fixed in `_check_trading_frequency` and #100 in its noun. Three faults
        in one f-string:

        * "A significant portion of trades" named no denominator and no scope.
          The scope is a single PTR -- five rows over three weeks, in the case
          that surfaced this -- and a reader with the filing open could not
          reconcile "a significant portion" against anything.
        * "This unusual concentration may warrant further review" asserts
          unusualness, and nothing measures it. `sector_concentration` is in
          `significance.NO_NULL_MODEL`, so no null distribution exists for it,
          and since #100 the only thing entitled to grade extremity is
          `percentile_rank` -- which put that finding at the 40th percentile of
          its own type while the sentence called it unusual. D3, D10 and D14
          were each spent removing a claim of exactly this shape.
        * The band hid a number already on the card. `computed_value` is the
          exact percentage and renders as the "Value" chip two lines below, so
          "60-75%" sat beside "60 percent". D5 does not reach this: it governs
          figures DERIVED from disclosed amount ranges, and a count of rows in
          a sector is disclosed exactly.

        `disclosed` carries the filing's household rows so the sentence can name
        what it left out, exactly as the frequency check does -- the denominator
        is the member's own rows, per #99, and a filing printing more lines than
        the finding counts is otherwise unreconcilable.
        """
        anomalies = []

        # Group transactions by disclosure (by year)
        disclosures_map = defaultdict(list)

        for txn in transactions:
            if txn.disclosure_id:
                disclosures_map[txn.disclosure_id].append(txn)

        # The rows in the same filings that are NOT the member's. Not counted;
        # named, so a reader comparing against the PDF can see why the numbers
        # differ.
        household: Dict[Any, list] = defaultdict(list)
        own_ids = {id(t) for t in transactions}
        for txn in disclosed or []:
            if txn.disclosure_id and id(txn) not in own_ids:
                household[txn.disclosure_id].append(txn)

        # Check each disclosure independently
        for disclosure_id, txns in disclosures_map.items():
            if len(txns) < self.min_trades_for_concentration:
                continue

            # Classify transactions by sector. A trade spanning two sectors
            # counts toward both rather than toward whichever the old table
            # happened to list first.
            sector_counts: Dict[str, int] = defaultdict(int)
            total_trades = 0

            for txn in txns:
                sectors = index.classify(txn.ticker, txn.description)
                if sectors:
                    for sector in sectors:
                        sector_counts[sector] += 1
                else:
                    sector_counts["other"] += 1

                total_trades += 1

            # Check for concentration
            for sector, count in sector_counts.items():
                if sector == "other":
                    continue

                concentration_percent = (count / total_trades) * 100

                if concentration_percent > self.concentration_threshold_percent:
                    anomalies.append(
                        {
                            "member_id": member_id,
                            "disclosure_id": disclosure_id,
                            "anomaly_type": "sector_concentration",
                            "severity": min(10, 5 + int((concentration_percent - 50) / 10)),
                            "title": (
                                f"High concentration in {sector} sector ({count} of {total_trades})"
                            ),
                            "description": (
                                f"{count} of the {total_trades} transactions attributed to "
                                f"this member in this filing are in the {sector} sector "
                                f"({_percent(concentration_percent)}), above the "
                                f"{self.concentration_threshold_percent:.0f}% threshold."
                                f"{_excluded_from_filing(household.get(disclosure_id))}"
                            ),
                            "computed_value": Decimal(str(concentration_percent)),
                            "threshold_value": Decimal(str(self.concentration_threshold_percent)),
                        }
                    )

        return anomalies

    def _check_trading_frequency(
        self,
        transactions: List[Transaction],
        member_id: int,
        member: Member,
        disclosed: List[Transaction] | None = None,
    ) -> List[Dict[str, Any]]:
        """How many trades the member made in a month, said so it can be checked.

        Two corrections, both found by auditing what this publishes against the
        filings it publishes about.

        **A month is a month.** This grouped by DISCLOSURE first and month
        second, so a member who reports one month across two PTRs produced two
        findings, each counting part of it, each titled "in <Month Year>". Sen.
        Boozman filed 17 August 2025 trades and 21 more on the same day: two
        findings saying 17 and 21 about a month containing 38. Measured on a
        10,594-row corpus, 128 member-months are split across more than one
        filing and they carry 2,668 rows -- a quarter of the corpus, not an
        edge case. The grouping is now by month across every filing that
        reports it.

        **The count is the member's own, and the sentence has to say so.**
        #99 stopped this counting a spouse's trades, which was right, and left
        the wording alone, which was not. Rep. Byron Donalds' filing prints 48
        transactions; 23 are his and 25 his spouse's. Publishing "23
        transactions were disclosed in March 2025" against a document showing
        48 reads as a site that cannot count -- the same "right number, wrong
        noun" defect this file already carries a fix for, committed by the fix
        for the one above it.

        `disclosed` is the whole household for the member, which
        `analyze_member` holds anyway. The rows this count leaves out are named
        rather than dropped silently.
        """
        anomalies = []

        if len(transactions) < 2:
            return []

        # Every filing that reports the month, not one at a time.
        monthly: dict[str, list] = defaultdict(list)
        for txn in transactions:
            if txn.transaction_date:
                monthly[txn.transaction_date.strftime("%Y-%m")].append(txn)

        # The household rows for the same month, for the sentence below. These
        # are NOT counted; they are disclosed so a reader comparing against the
        # filing can see why the numbers differ.
        household: dict[str, list] = defaultdict(list)
        own_ids = {id(t) for t in transactions}
        for txn in disclosed or []:
            if txn.transaction_date and id(txn) not in own_ids:
                household[txn.transaction_date.strftime("%Y-%m")].append(txn)

        if True:
            # Check for high-frequency months
            for month, rows in monthly.items():
                rows, exchanged = _corporate_actions(rows)
                count = len(rows)
                # The filing that reported most of the month, so the finding
                # links somewhere real when the month spans several.
                by_filing = Counter(t.disclosure_id for t in rows if t.disclosure_id)
                disclosure_id = (
                    min(by_filing, key=lambda d: (-by_filing[d], d)) if by_filing else None
                )
                if count > self.frequency_threshold_per_month:
                    # Format month from YYYY-MM to "Month Year"
                    try:
                        from datetime import datetime

                        date_obj = datetime.strptime(month, "%Y-%m")
                        formatted_month = date_obj.strftime("%B %Y")
                    except ValueError:
                        formatted_month = month

                    # The count, exactly, and the band it used to be published
                    # as is gone. Three reasons, in order of how badly the band
                    # failed:
                    #
                    # The sentence assumed every band was closed. "more than
                    # 100" in a slot reading `f"Between {trade_range} stock
                    # trades"` published "Between more than 100 stock trades
                    # were made in March 2026" beside a named senator, 24 times.
                    #
                    # Its lowest band could not occur. This fires on
                    # `count > threshold`, so the smallest count it can produce
                    # is 11, while the band claimed a floor of 10 in the same
                    # sentence that named 10 as the threshold it exceeded.
                    #
                    # And the band was hiding a number already on the card:
                    # `computed_value` is the exact count, rendered as the
                    # "Value" chip two lines from the title. "more than 100"
                    # and "701" were published side by side.
                    #
                    # The banding cited D5, which does not reach this. D5 is
                    # about figures DERIVED from disclosed ranges -- "any point
                    # estimate derived from them is fabricated precision". A
                    # count of transactions in a month is disclosed exactly, the
                    # same class of fact `clustering.py` publishes exactly and
                    # calls "dates, tickers and directions ... disclosed
                    # exactly". Rounding an exact fact is not honesty about
                    # uncertainty; it is discarding precision the filing gave.
                    anomalies.append(
                        {
                            "member_id": member_id,
                            "disclosure_id": disclosure_id,
                            "anomaly_type": "high_trading_frequency",
                            "severity": min(10, 4 + (count - self.frequency_threshold_per_month)),
                            "title": (
                                f"High trading activity: {count} trades in {formatted_month}"
                            ),
                            # The closing sentence used to read "High trading
                            # frequency may indicate active trading based on
                            # non-public information." The anomalies page renders
                            # the catalogue's `limits` directly beneath it --
                            # "Trading often is not trading improperly, and a
                            # managed account can produce this without the member
                            # choosing any of it" -- so the detector was asserting
                            # on one line what the page denied on the next. D3,
                            # D10 and D14 were all spent removing exactly this
                            # kind of claim; it survived here.
                            # "stock" was never checked against anything. The
                            # PTR row carries no asset type, so every
                            # transaction was described as a stock trade --
                            # including Sen. Rick Scott's eighteen, which were
                            # municipal bonds, and Sen. Fetterman's, which were
                            # bonds in a child's account. The count was right
                            # and the noun was invented.
                            #
                            # The form prints the asset class in brackets and
                            # the parser keeps it, so where every row in the
                            # month is debt the sentence says debt. Where the
                            # classes are mixed or unread it says
                            # "transactions", which is what a PTR row is
                            # whatever it holds.
                            "description": (
                                f"{count} {_what_was_traded(rows)} attributed to this "
                                f"member in {formatted_month}, above the threshold of "
                                f"{self.frequency_threshold_per_month} per month."
                                f"{_over_how_many_days(rows)}"
                                f"{_excluded_clause(household.get(month))}"
                                f"{_exchange_clause(exchanged)}"
                            ),
                            "computed_value": Decimal(str(count)),
                            "threshold_value": Decimal(str(self.frequency_threshold_per_month)),
                        }
                    )

        return anomalies

    def _check_large_trades(
        self, transactions: List[Transaction], member_id: int, member: Member
    ) -> List[Dict[str, Any]]:
        """Flag unusually large trades (over $1M)."""
        anomalies = []
        large_trade_threshold = Decimal("1000000")  # $1M

        for txn in transactions:
            if txn.amount_min and txn.amount_min > large_trade_threshold:
                text = self._build_large_trade_text(txn)

                anomalies.append(
                    {
                        "member_id": member_id,
                        "disclosure_id": txn.disclosure_id,
                        "transaction_id": txn.id,
                        "anomaly_type": "large_trade",
                        "severity": min(10, 5 + int(txn.amount_min / Decimal("5000000"))),
                        "title": text["title"],
                        "description": text["description"],
                        "computed_value": txn.amount_min,
                        "threshold_value": large_trade_threshold,
                    }
                )

        return anomalies

    def analyze_all_members(self, db: Session) -> Dict[str, Any]:
        """
        Analyze all members for trade anomalies.

        Args:
            db: Database session

        Returns:
            Summary with all detected anomalies
        """
        from src.config import get_settings

        disabled_types = get_settings().disabled_anomaly_types_set

        # Only a member who has traded can produce a trade finding:
        # `analyze_member` returns immediately when a member has no
        # transactions. Walking the whole roster to discover that cost two
        # queries per member -- against a 12,770-name roster imported from
        # Congress.gov, that is ~25,000 round trips to produce nothing, and it
        # dominated the runtime of the analysis step.
        #
        # Scoping the loop is behaviour-preserving by construction: the members
        # dropped here are exactly the ones whose analysis returned [] anyway.
        # The only thing that changes is `members_analyzed`, which now reports
        # how many were actually analysed rather than how many exist.
        #
        # Retired members are still included -- membership of this set is
        # decided by having traded, not by being in office.
        traded = db.query(Disclosure.member_id).join(
            Transaction, Transaction.disclosure_id == Disclosure.id
        )
        member_ids = [row[0] for row in traded.distinct()]
        members = db.query(Member).filter(Member.id.in_(member_ids)).all() if member_ids else []

        all_anomalies: List[Dict[str, Any]] = []
        members_analyzed = 0
        members_with_anomalies = 0
        seen: set[tuple] = set()
        # See `stored_by_identity`. The rows matter here, not just the keys: a
        # trade-level finding is restated in place below rather than duplicated.
        stored = stored_by_identity(db)

        self._sync_large_trade_anomalies(db)
        self._large_trades_synced = True

        for member in members:
            anomalies = self.analyze_member(db, member.id, member=member)
            members_analyzed += 1
            if members_analyzed % PROGRESS_EVERY_MEMBERS == 0:
                logger.info(
                    "Trade analysis: %d/%d members, %d findings so far",
                    members_analyzed,
                    len(members),
                    len(all_anomalies),
                )

            if anomalies:
                members_with_anomalies += 1

                for anomaly in anomalies:
                    # One definition of "the same finding", shared with the
                    # other writers and mirrored by the unique indexes:
                    # src/analysis/anomaly_key.py. A finding about a trade is
                    # keyed by the trade; one about the member, by its title.
                    #
                    # The member-level branch used to key on disclosure_id as
                    # well, which the schema has no way to enforce: the same
                    # monthly trading-frequency finding can be attributed to two
                    # different disclosures, and both rows passed this check and
                    # then collided in the database.
                    key = identity_of(anomaly)
                    if key is None or key in seen:
                        continue

                    existing = stored.get(key)
                    if existing is not None:
                        # A trade-level finding is allowed to be restated: the
                        # trade is the same, so the row is updated in place
                        # rather than duplicated.
                        if (
                            existing.title != anomaly["title"]
                            or existing.description != anomaly["description"]
                            or existing.disclosure_id != anomaly.get("disclosure_id")
                        ):
                            existing.title = anomaly["title"]
                            existing.description = anomaly["description"]
                            existing.disclosure_id = anomaly.get("disclosure_id")
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
                        transaction_id=anomaly.get("transaction_id"),
                        anomaly_type=anomaly["anomaly_type"],
                        severity=anomaly["severity"],
                        title=anomaly["title"],
                        description=anomaly["description"],
                        computed_value=anomaly.get("computed_value"),
                        threshold_value=anomaly.get("threshold_value"),
                    )
                    db.add(db_anomaly)
                    # The query above is blind to rows added earlier in this
                    # loop: SessionLocal is autoflush=False.
                    seen.add(key)

                    all_anomalies.append(
                        {
                            **anomaly,
                            "member_name": f"{member.first_name} {member.last_name}",
                            "state": member.state,
                            "party": member.party.value,
                        }
                    )

        db.commit()

        return {
            "members_analyzed": members_analyzed,
            "members_with_anomalies": members_with_anomalies,
            "total_anomalies": len(all_anomalies),
            "anomalies": all_anomalies,
        }


def analyze_trades(db: Session, member_id: int | None = None) -> Dict[str, Any]:
    """
    Convenience function to run trade analysis.

    Args:
        db: Database session
        member_id: Optional specific member to analyze

    Returns:
        Analysis results
    """
    analyzer = TradeAnalyzer()

    if member_id:
        anomalies = analyzer.analyze_member(db, member_id)
        return {
            "member_id": member_id,
            "total_anomalies": len(anomalies),
            "anomalies": anomalies,
        }
    else:
        return analyzer.analyze_all_members(db)
