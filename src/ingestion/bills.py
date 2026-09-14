"""Bills, sponsorships and committee referrals from the Congress.gov API.

Feeds `Bill`, `BillSponsorship` and `BillCommittee`, which
:mod:`src.analysis.legislation` joins against trades. This is what lets the
project ask whether a member acted on an issuer's industry in their official
capacity near the time they traded it -- the question the free trade-listing
sites do not answer.

Congress.gov is the official API of the Library of Congress: public domain, and
a free key from https://api.congress.gov/sign-up/ allows **20,000 requests per
hour**, twenty times the FEC budget. It is also the only route available: the
same data on clerk.house.gov, senate.gov and govinfo.gov is unreachable from
this deployment's network policy.

Why it is driven by member
--------------------------
`/member/{bioguide}/sponsored-legislation` returns the bill's `policyArea`
*inline* and covers every congress the member has served in, in one paginated
sweep. That means the whole sponsorship dataset costs about eight pages per
member -- roughly 4,300 requests for all of Congress -- with no per-bill detail
fetch at all. Walking `/bill/{congress}` instead would mean ~20,000 bills and a
follow-up request each to find out who sponsored them.

Committee referrals are the exception: they are a separate request per bill, so
they are fetched **lazily**, only for bills that already matched a member's
trading. `Bill.committees_fetched` records which bills have been looked at, so
"no committees" stays distinguishable from "not looked up yet".

Sponsor is not cosponsor
------------------------
Both are ingested, because the data is cheap and cosponsorship is worth having.
They are stored separately because they are not the same act: measured on the
live API, Sharice Davids has sponsored 71 bills and cosponsored 1,562.
Cosponsoring is 22x more common and very nearly costless, so a detector treating
them alike would fire on everybody. Only sponsorship currently produces a
finding; see :mod:`src.analysis.legislation`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterator, List

import requests
from sqlalchemy.orm import Session

from src.db.models import Bill, BillCommittee, BillSponsorship, Member
from src.ingestion.rate_limit import HOUR, RequestBudgetExhausted, ThrottledClient

logger = logging.getLogger(__name__)

CONGRESS_API_BASE_URL = "https://api.congress.gov/v3"

REQUEST_TIMEOUT = 60

# Congress.gov reports x-ratelimit-limit: 20000 per hour. Headroom for the same
# reason as everywhere else: a run that trips the limit wastes the rest of the
# hour, not just the request.
DEFAULT_REQUESTS_PER_HOUR = 18000

# The API caps `limit` at 250.
PAGE_SIZE = 250


class CongressAPIClient(ThrottledClient):
    """Congress.gov client. Identifies itself with an `api_key` parameter."""

    base_url = CONGRESS_API_BASE_URL
    name = "Congress.gov"

    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        requests_per_hour: int = DEFAULT_REQUESTS_PER_HOUR,
        max_requests: int | None = None,
        **kwargs: Any,
    ):
        if not api_key:
            raise ValueError(
                "CONGRESS_GOV_API_KEY is not set. "
                "Get a free key at https://api.congress.gov/sign-up/"
            )
        self.api_key = api_key
        super().__init__(
            limit=requests_per_hour,
            window_seconds=HOUR,
            session=session,
            timeout=REQUEST_TIMEOUT,
            max_requests=max_requests,
            **kwargs,
        )

    def _auth_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params["api_key"] = self.api_key
        params.setdefault("format", "json")
        return params

    def paginate(self, path: str, key: str) -> Iterator[Dict[str, Any]]:
        """Page through a list endpoint by offset.

        Congress.gov signals more pages with a `pagination.next` URL. That URL
        already carries the key, so it is not followed directly -- the offset is
        tracked here instead, which keeps the credential out of anything logged.
        """
        offset = 0
        while True:
            body = self.get(path, {"limit": PAGE_SIZE, "offset": offset})
            items = body.get(key) or []
            yield from items

            if not items or not (body.get("pagination") or {}).get("next"):
                return
            offset += PAGE_SIZE


def system_code_to_thomas_id(system_code: str | None) -> str | None:
    """Convert a Congress.gov committee systemCode to a congress-legislators id.

    Congress.gov publishes "hswm00" for a full committee and "hsba16" for a
    subcommittee. congress-legislators -- and therefore
    `CommitteeAssignment.committee_id` -- uses "HSWM" and "HSBA16". So the rule
    is uppercase, then drop a trailing "00". Verified against both sources
    rather than inferred from one.
    """
    if not system_code:
        return None
    code = system_code.strip().upper()
    if not code:
        return None
    if len(code) > 2 and code.endswith("00"):
        return code[:-2]
    return code


def _walk_committees(committees: Any) -> Iterator[Dict[str, Any]]:
    """Yield each committee and, separately, each of its subcommittees.

    Congress.gov nests subcommittees inside their parent, each with its own
    systemCode and its own activity dates. They are worth storing in their own
    right: a referral to the Energy Subcommittee is a sharper jurisdiction
    signal than a referral to Energy and Commerce, and it is usually later. A
    member seated only on the subcommittee is exactly the case this detector
    exists for.
    """
    for committee in committees or []:
        yield committee
        for sub in committee.get("subcommittees") or []:
            # Subcommittee entries carry no chamber of their own.
            yield {**sub, "chamber": sub.get("chamber") or committee.get("chamber")}


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _upsert_bill(db: Session, payload: Dict[str, Any]) -> Bill | None:
    """Store or refresh one bill, returning it. None if it is unidentifiable."""
    congress = payload.get("congress")
    bill_type = (payload.get("type") or "").strip().lower()
    number = str(payload.get("number") or "").strip()
    if not congress or not bill_type or not number:
        return None

    bill = (
        db.query(Bill)
        .filter(
            Bill.congress == congress,
            Bill.bill_type == bill_type,
            Bill.number == number,
        )
        .first()
    )
    if bill is None:
        bill = Bill(congress=congress, bill_type=bill_type, number=number)
        db.add(bill)

    latest = payload.get("latestAction") or {}
    bill.title = payload.get("title") or bill.title
    # A null never overwrites a value we already have. The same bill arrives
    # once per member who touched it, and an older or thinner response must not
    # erase a policy area a richer one supplied.
    bill.policy_area = (payload.get("policyArea") or {}).get("name") or bill.policy_area
    bill.introduced_date = _parse_date(payload.get("introducedDate")) or bill.introduced_date
    bill.latest_action_date = _parse_date(latest.get("actionDate")) or bill.latest_action_date
    bill.latest_action_text = latest.get("text") or bill.latest_action_text
    bill.origin_chamber = payload.get("originChamber") or bill.origin_chamber

    db.flush()
    return bill


def _sponsorship_pages(client, bioguide_id: str, path: str, key: str, skipped: List[str]):
    """Pages of one member's legislation, or nothing if Congress.gov lacks them.

    Congress.gov answers 404 for a bioguide id it does not carry, and this
    project's roster holds every member in history -- so one id it has never
    heard of aborted the sponsorship ingest for all 12,770 of them, leaving two
    detectors with no data at all:

        404 Client Error: Not Found for url:
        https://api.congress.gov/v3/member/M000633/sponsored-legislation

    A member it cannot resolve is a gap in that member, not a reason to abandon
    everyone after them. Anything other than a 404 still propagates -- a 403 on
    a bad key, or a 429, must not be mistaken for "this member has no bills".
    """
    try:
        yield from client.paginate(f"/member/{bioguide_id}/{path}", key)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status != 404:
            raise
        skipped.append(bioguide_id)
        logger.debug("Congress.gov has no record for %s; skipping", bioguide_id)


def ingest_member_bills(
    db: Session,
    api_key: str,
    *,
    client: CongressAPIClient | None = None,
    max_requests: int | None = None,
    include_cosponsored: bool = True,
    bioguide_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Ingest sponsored (and optionally cosponsored) legislation for members."""
    client = client or CongressAPIClient(api_key, max_requests=max_requests)

    members = db.query(Member)
    if bioguide_ids:
        members = members.filter(Member.bioguide_id.in_(bioguide_ids))
    roster = members.all()

    if not roster:
        logger.warning("No members in the database. Run `ingest` first to populate the roster.")
        return {
            "members_queried": 0,
            "bills": 0,
            "sponsorships": 0,
            "cosponsorships": 0,
            "bills_without_policy_area": 0,
            "bills_mapped_to_a_sector": 0,
            "requests_made": client.requests_made,
            "stopped_early": False,
        }

    from src.analysis.sectors import policy_area_sectors

    sponsorships = 0
    cosponsorships = 0
    members_queried = 0
    stopped_early = False
    seen_bills: set[int] = set()
    unknown_to_congress_gov: List[str] = []

    kinds = [("sponsored-legislation", "sponsoredLegislation", True)]
    if include_cosponsored:
        kinds.append(("cosponsored-legislation", "cosponsoredLegislation", False))

    try:
        for member in roster:
            members_queried += 1
            for path, key, is_sponsor in kinds:
                pages = _sponsorship_pages(
                    client, member.bioguide_id, path, key, unknown_to_congress_gov
                )
                for payload in pages:
                    bill = _upsert_bill(db, payload)
                    if bill is None:
                        continue
                    seen_bills.add(bill.id)

                    exists = (
                        db.query(BillSponsorship)
                        .filter(
                            BillSponsorship.bill_id == bill.id,
                            BillSponsorship.member_id == member.id,
                            BillSponsorship.is_sponsor == is_sponsor,
                        )
                        .first()
                    )
                    if exists:
                        continue

                    db.add(
                        BillSponsorship(bill_id=bill.id, member_id=member.id, is_sponsor=is_sponsor)
                    )
                    if is_sponsor:
                        sponsorships += 1
                    else:
                        cosponsorships += 1
            db.commit()

    except RequestBudgetExhausted as exc:
        logger.warning("Congress.gov ingest stopped early: %s", exc)
        stopped_early = True

    db.commit()

    # Report the ceiling rather than let it pass silently: most bills cannot be
    # mapped to a sector, and a detector that finds nothing should be readable
    # as "little was mappable" instead of "nothing was there".
    unclassified = db.query(Bill).filter(Bill.policy_area.is_(None)).count()
    total_bills = db.query(Bill).count()
    mapped = sum(
        1
        for (area,) in db.query(Bill.policy_area).filter(Bill.policy_area.isnot(None)).all()
        if policy_area_sectors(area)
    )

    logger.info(
        "Bills: %d stored, %d without a policy area, %d in a sector-mapped area; "
        "%d sponsorships and %d cosponsorships across %d members in %d requests",
        total_bills,
        unclassified,
        mapped,
        sponsorships,
        cosponsorships,
        members_queried,
        client.requests_made,
    )

    if unknown_to_congress_gov:
        # Counted and said out loud. A silent skip would make a roster drifting
        # away from Congress.gov's ids look exactly like Congress passing no
        # legislation.
        logger.warning(
            "Congress.gov had no record for %d of %d members queried (e.g. %s)",
            len(unknown_to_congress_gov),
            members_queried,
            ", ".join(unknown_to_congress_gov[:5]),
        )
    return {
        "members_queried": members_queried,
        "bills": total_bills,
        "sponsorships": sponsorships,
        "cosponsorships": cosponsorships,
        "bills_without_policy_area": unclassified,
        "bills_mapped_to_a_sector": mapped,
        "requests_made": client.requests_made,
        "stopped_early": stopped_early,
    }


def fetch_bill_committees(
    db: Session,
    api_key: str,
    *,
    bills: List[Bill] | None = None,
    client: CongressAPIClient | None = None,
    max_requests: int | None = None,
) -> Dict[str, Any]:
    """Fetch committee referrals for bills that do not have them yet.

    One request per bill, which is why `bills` is normally passed by the caller
    after narrowing to the ones that actually matched a member's trading.
    Without an argument it fills in every unfetched bill, which is fine for a
    small database and expensive for a full one.
    """
    client = client or CongressAPIClient(api_key, max_requests=max_requests)

    targets = bills if bills is not None else db.query(Bill).filter(~Bill.committees_fetched).all()

    fetched = 0
    referrals = 0
    stopped_early = False
    seen_referrals: set[tuple[Any, ...]] = set()

    try:
        for bill in targets:
            if bill.committees_fetched:
                continue
            body = client.get(f"/bill/{bill.congress}/{bill.bill_type}/{bill.number}/committees")
            fetched += 1

            for committee in _walk_committees(body.get("committees")):
                thomas_id = system_code_to_thomas_id(committee.get("systemCode"))
                if not thomas_id:
                    continue

                for activity in committee.get("activities") or [{}]:
                    name = activity.get("name")
                    when = _parse_date(activity.get("date"))

                    # The existence check below queries the database, which
                    # cannot see rows added earlier in this same uncommitted
                    # batch -- and a committee really does repeat an activity
                    # name within one response (Natural Resources logged
                    # "Unknown" twice, seven minutes apart, on H.R. 1 of the
                    # 118th). Without the in-batch set that is an
                    # IntegrityError that loses the whole bill.
                    key = (bill.id, thomas_id, name, when)
                    if key in seen_referrals:
                        continue
                    seen_referrals.add(key)

                    exists = (
                        db.query(BillCommittee)
                        .filter(
                            BillCommittee.bill_id == bill.id,
                            BillCommittee.committee_id == thomas_id,
                            BillCommittee.activity == name,
                            BillCommittee.activity_date == when,
                        )
                        .first()
                    )
                    if exists:
                        continue
                    db.add(
                        BillCommittee(
                            bill_id=bill.id,
                            committee_id=thomas_id,
                            committee_name=committee.get("name"),
                            chamber=committee.get("chamber"),
                            activity=name,
                            activity_date=when,
                        )
                    )
                    referrals += 1

            bill.committees_fetched = True
            db.commit()

    except RequestBudgetExhausted as exc:
        logger.warning("Committee referral fetch stopped early: %s", exc)
        stopped_early = True

    db.commit()

    logger.info(
        "Committee referrals: %d bills looked up, %d referrals stored, %d requests",
        fetched,
        referrals,
        client.requests_made,
    )
    return {
        "bills_looked_up": fetched,
        "referrals": referrals,
        "requests_made": client.requests_made,
        "stopped_early": stopped_early,
    }
