from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Chamber(str, Enum):
    """Congressional chamber."""

    HOUSE = "house"
    SENATE = "senate"


class Party(str, Enum):
    """Political party."""

    DEMOCRAT = "D"
    REPUBLICAN = "R"
    INDEPENDENT = "I"
    OTHER = "O"


class TransactionType(str, Enum):
    """Type of financial transaction."""

    PURCHASE = "purchase"
    SALE = "sale"
    EXCHANGE = "exchange"


class AssetType(str, Enum):
    """Type of asset."""

    STOCK = "stock"
    BOND = "bond"
    MUTUAL_FUND = "mutual_fund"
    REAL_ESTATE = "real_estate"
    RETIREMENT = "retirement"
    BANK_ACCOUNT = "bank_account"
    OTHER = "other"


class Member(Base):
    """Congressional member."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    bioguide_id: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    chamber: Mapped[Chamber] = mapped_column(SQLEnum(Chamber))
    party: Mapped[Party] = mapped_column(SQLEnum(Party))
    state: Mapped[str] = mapped_column(String(2))
    district: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Current status
    in_office: Mapped[bool] = mapped_column(default=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Materialized counts for fast sorting/filtering
    anomaly_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    disclosure_count: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    disclosures: Mapped[List["Disclosure"]] = relationship(
        "Disclosure", back_populates="member", cascade="all, delete-orphan"
    )
    anomalies: Mapped[List["Anomaly"]] = relationship(
        "Anomaly", back_populates="member", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Member {self.first_name} {self.last_name} ({self.party.value}-{self.state})>"


class Disclosure(Base):
    """Financial disclosure filing."""

    __tablename__ = "disclosures"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)

    # Filing info
    filing_year: Mapped[int] = mapped_column(Integer, index=True)
    filing_type: Mapped[str] = mapped_column(String(50))
    filing_date: Mapped[datetime] = mapped_column(DateTime)
    document_id: Mapped[str] = mapped_column(String(100), unique=True)
    document_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # PTR (Periodic Transaction Report) flag
    is_ptr: Mapped[bool] = mapped_column(default=False, index=True)

    # Parsing status. `parsed` means the parser ran without raising -- it has
    # never meant the parse worked, and a filing that yielded nothing at all
    # used to be indistinguishable from one read cleanly.
    parsed: Mapped[bool] = mapped_column(default=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How much of the document the parser actually read, 0-1. A completeness
    # ratio: rows read over rows that looked like records, times fields
    # extracted over fields required. See src/parsing/confidence.py.
    #
    # Null means not scored yet -- a filing parsed before this existed -- never
    # "scored and fine". Nothing is excluded from analysis on the strength of
    # it: a missing trade is already invisible, and dropping the ones known to
    # be shaky would compound that silently.
    parse_confidence: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    # The named reasons behind the score, which is what a person acts on.
    parse_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    member: Mapped["Member"] = relationship("Member", back_populates="disclosures")
    assets: Mapped[List["Asset"]] = relationship(
        "Asset", back_populates="disclosure", cascade="all, delete-orphan"
    )
    transactions: Mapped[List["Transaction"]] = relationship(
        "Transaction", back_populates="disclosure", cascade="all, delete-orphan"
    )
    liabilities: Mapped[List["Liability"]] = relationship(
        "Liability", back_populates="disclosure", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_disclosures_member_year", "member_id", "filing_year"),
        Index("ix_disclosures_ptr", "is_ptr", "filing_year"),
    )

    def __repr__(self) -> str:
        ptr_label = " PTR" if self.is_ptr else ""
        return f"<Disclosure{ptr_label} {self.document_id} ({self.filing_year})>"


class Asset(Base):
    """Asset reported in a disclosure."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    disclosure_id: Mapped[int] = mapped_column(ForeignKey("disclosures.id"), index=True)

    # Asset info
    asset_type: Mapped[AssetType] = mapped_column(SQLEnum(AssetType))
    description: Mapped[str] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # Value range
    value_min: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    value_max: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    # Income from this asset
    income_min: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    income_max: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    income_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    disclosure: Mapped["Disclosure"] = relationship("Disclosure", back_populates="assets")

    def __repr__(self) -> str:
        return f"<Asset {self.description[:30]}...>"


class Transaction(Base):
    """Stock or asset transaction."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    disclosure_id: Mapped[int] = mapped_column(ForeignKey("disclosures.id"), index=True)

    # Transaction info
    transaction_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    transaction_type: Mapped[TransactionType] = mapped_column(SQLEnum(TransactionType))
    description: Mapped[str] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # Amount range
    amount_min: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    amount_max: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    # Owner
    owner: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    disclosure: Mapped["Disclosure"] = relationship("Disclosure", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction {self.transaction_type.value} {self.ticker}>"


class Liability(Base):
    """Liability/debt reported in a disclosure."""

    __tablename__ = "liabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    disclosure_id: Mapped[int] = mapped_column(ForeignKey("disclosures.id"), index=True)

    # Liability info
    creditor: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    liability_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Amount range
    amount_min: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    amount_max: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    disclosure: Mapped["Disclosure"] = relationship("Disclosure", back_populates="liabilities")

    def __repr__(self) -> str:
        return f"<Liability {self.creditor}>"


# The severity column is free text, and three detector families historically
# wrote three different vocabularies into it: "HIGH"/"CRITICAL" (advanced,
# extended, tier-2), "high"/"medium"/"low" (trade late-filing), and raw ints
# 4-10 (trade large-trade/frequency). Filtering with `severity == "high"` then
# silently missed every "HIGH" row. Normalization is enforced on the model so
# it applies to every write path, including the two analyzers that construct
# Anomaly() directly instead of going through persist_anomalies().
SEVERITIES = ("low", "medium", "high")


def normalize_severity(severity: object) -> str:
    """Coerce any detector's severity into the API vocabulary."""
    if severity is None:
        return "medium"
    if isinstance(severity, bool):
        return "medium"
    if isinstance(severity, (int, float)):
        return "high" if severity >= 8 else "medium" if severity >= 5 else "low"

    text = str(severity).strip().lower()
    if text in SEVERITIES:
        return text
    if text == "critical":
        return "high"
    # Numeric strings reach here because some detectors stringify their scores.
    try:
        return normalize_severity(float(text))
    except ValueError:
        return "medium"


class Anomaly(Base):
    """Detected anomaly or flag for a member."""

    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)

    # Anomaly info
    anomaly_type: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)

    # Related data
    disclosure_id: Mapped[int | None] = mapped_column(ForeignKey("disclosures.id"), nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)

    # Computed values
    computed_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)

    # Where this finding sits among others of the same type, 0-100. Thresholds
    # in this codebase are asserted rather than calibrated, so "top 2% of
    # findings of this type" is a far more defensible statement than "exceeded
    # threshold 100". Null when the population is too small to rank against.
    # Maintained by src.analysis.baselines.annotate_percentile_ranks.
    percentile_rank: Mapped[float | None] = mapped_column(Float, nullable=True)

    # How often chance alone produces a coincidence this strong (`p_value`), and
    # the same after Benjamini-Hochberg correction for every test in the run
    # (`q_value`). Maintained by src.analysis.significance.
    #
    # NULL means "no null model exists for this detector", never "passed". Only
    # the timing-coincidence detectors admit one; a magnitude rule like
    # sector_concentration has no null to shift, and inventing a p-value for it
    # would be the exact failure this project exists to avoid. Those keep
    # percentile_rank, which is the right tool for a magnitude.
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_value: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)

    # Metadata
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed: Mapped[bool] = mapped_column(default=False)

    # Relationships
    member: Mapped["Member"] = relationship("Member", back_populates="anomalies")

    # Matches the dedupe key persist_anomalies() checks in Python. Declared here
    # as well as in migration c3a7f1d92b04 so metadata.create_all() (used by the
    # test suite) builds the same schema alembic does.
    __table_args__ = (
        Index(
            "uq_anomaly_member_type_title",
            "member_id",
            "anomaly_type",
            "title",
            unique=True,
        ),
    )

    @validates("severity")
    def _validate_severity(self, _key: str, value: object) -> str:
        return normalize_severity(value)

    def __repr__(self) -> str:
        return f"<Anomaly {self.anomaly_type}: {self.title[:30]}...>"


class CommitteeAssignment(Base):
    """Which committees a member sits on.

    Sourced from unitedstates/congress-legislators, which is public domain and
    keys its membership file on bioguide IDs -- the same identifier Member
    already carries.

    This exists because `detect_committee_conflicts` had no committee data at
    all: its SAMPLE_COMMITTEE_ASSIGNMENTS was an empty dict, so it fell back to
    substring-matching tickers against sector keywords, where "ba" matched
    "Alibaba".
    """

    __tablename__ = "committee_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)

    # thomas_id from the source data, e.g. "HSBA" (House Financial Services).
    committee_id: Mapped[str] = mapped_column(String(20), index=True)
    committee_name: Mapped[str] = mapped_column(String(200))
    chamber: Mapped[Chamber | None] = mapped_column(SQLEnum(Chamber), nullable=True)

    # Subcommittee ids are the parent id plus a numeric suffix; jurisdiction
    # questions usually want the parent, so keep them distinguishable.
    is_subcommittee: Mapped[bool] = mapped_column(default=False)
    parent_committee_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # "Chairman", "Ranking Member", or null for rank-and-file.
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    party: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    member: Mapped["Member"] = relationship("Member")

    __table_args__ = (
        Index(
            "uq_committee_assignment",
            "member_id",
            "committee_id",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<CommitteeAssignment {self.committee_id} member={self.member_id}>"


# ---------------------------------------------------------------------------
# Tier-2 datasets — driving "donor conflict", "lobbying overlap", and
# "contract front-run" detectors. Each row is a TRIGGER EVENT we cross-
# reference against transactions to detect conflict-of-interest patterns.
#
# All three carry `external_id`: the source's own identifier for the record
# (FEC sub_id, LDA filing_uuid, USASpending award id). It exists because the
# natural key is not unique in the real data -- Boeing's PAC gave Rick Larsen's
# committee $5,000 twice on 2024-12-31, primary and general, and deduplicating
# on (member, ticker, date, amount) silently merges them into one donation.
# ---------------------------------------------------------------------------


class CampaignDonation(Base):
    """A campaign donation from a public company to a member.

    Sourced from the FEC (`src.ingestion.fec`). The detector flags
    transactions in `ticker` made by `member_id` within ``donor_window_days``
    of `donation_date`.
    """

    __tablename__ = "campaign_donations"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)

    ticker: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    donor_name: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    cycle: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    donation_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(50), default="unknown")
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_donations_member_ticker", "member_id", "ticker"),
        Index("ix_donations_source_external", "source", "external_id", unique=True),
    )


class LobbyingDisclosure(Base):
    """A lobbying disclosure filed by a public company.

    Sourced from the Senate LDA (`src.ingestion.lda`). The detector
    flags transactions in `ticker` made within ``lobbying_window_days`` of
    `filed_date` regardless of which member traded — it's a market-wide
    signal that the issuer is actively trying to shape policy.
    """

    __tablename__ = "lobbying_disclosures"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    registrant: Mapped[str] = mapped_column(String(255))
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    filed_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    issue_codes: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(50), default="unknown")
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_lobbying_source_external", "source", "external_id", unique=True),)


class GovernmentContract(Base):
    """A federal contract awarded to a public company.

    Sourced from USASpending (`src.ingestion.usaspending`). The
    detector flags PURCHASE transactions in `ticker` made within
    ``contract_window_days`` *before* `awarded_date` — front-running an
    award is the clearest insider signal in this dataset.
    """

    __tablename__ = "government_contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    agency: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    awarded_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    source: Mapped[str] = mapped_column(String(50), default="unknown")
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_contracts_source_external", "source", "external_id", unique=True),)


# ---------------------------------------------------------------------------
# Legislative action — joining trades to what a member did in office.
#
# Every other table in this file describes trading. These three describe the
# member's official conduct, and the join between them is the thing no
# competitor sells: Capitol Trades, Unusual Whales and Quiver all publish the
# raw trade listings for free.
#
# Sourced from the Congress.gov API (`src.ingestion.bills`), which is official
# and public domain.
# ---------------------------------------------------------------------------


class Bill(Base):
    """A bill or resolution, with the policy area CRS assigned it.

    `policy_area` is the join key to a sector. It is nullable because CRS did
    not classify bills systematically until around the 111th Congress: measured
    on the live API, 67 of Pelosi's 199 sponsored bills lack one and all of them
    predate 2020, while only 1 of 9,896 bills across three sitting members' full
    histories is unclassified. For the 2024-25 window this project analyses the
    rate is effectively zero, but the column stays nullable so historical rows
    can be stored rather than dropped.
    """

    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(primary_key=True)

    congress: Mapped[int] = mapped_column(Integer, index=True)
    bill_type: Mapped[str] = mapped_column(String(10))  # hr, s, hres, sjres, ...
    number: Mapped[str] = mapped_column(String(20))

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_area: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    origin_chamber: Mapped[str | None] = mapped_column(String(20), nullable=True)

    introduced_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    latest_action_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latest_action_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set once the bill's committee referrals have been fetched. They cost one
    # request each and are only worth spending on bills that already passed the
    # sector-and-trade filter, so "no committees" and "not looked up yet" have
    # to be distinguishable.
    committees_fetched: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    sponsorships: Mapped[List["BillSponsorship"]] = relationship(
        "BillSponsorship", back_populates="bill", cascade="all, delete-orphan"
    )
    committees: Mapped[List["BillCommittee"]] = relationship(
        "BillCommittee", back_populates="bill", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("uq_bill_identity", "congress", "bill_type", "number", unique=True),)

    @property
    def citation(self) -> str:
        """The form a reader can look up on congress.gov, e.g. "H.R. 4644"."""
        return f"{self.bill_type.upper()} {self.number}"

    def __repr__(self) -> str:
        return f"<Bill {self.congress} {self.citation}>"


class BillSponsorship(Base):
    """A member sponsoring or cosponsoring a bill.

    `is_sponsor` is not a detail. Measured on real data, Sharice Davids has
    sponsored 71 bills and cosponsored 1,562 -- cosponsoring is 22x more common
    and very nearly costless, so the two are different acts carrying very
    different weight. Both are stored; only sponsorship is currently allowed to
    produce a finding.
    """

    __tablename__ = "bill_sponsorships"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)

    is_sponsor: Mapped[bool] = mapped_column(default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    bill: Mapped["Bill"] = relationship("Bill", back_populates="sponsorships")
    member: Mapped["Member"] = relationship("Member")

    __table_args__ = (
        Index("uq_bill_sponsorship", "bill_id", "member_id", "is_sponsor", unique=True),
    )


class BillCommittee(Base):
    """A committee a bill was referred to.

    `committee_id` is stored in congress-legislators' thomas_id form -- "HSWM",
    or "HSBA16" for a subcommittee -- so it joins directly to
    `CommitteeAssignment.committee_id`. Congress.gov publishes it as a
    `systemCode` ("hswm00", "hsba16"); the conversion is uppercase, then drop a
    trailing "00". Verified against both sources rather than assumed.
    """

    __tablename__ = "bill_committees"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("bills.id"), index=True)

    committee_id: Mapped[str] = mapped_column(String(20), index=True)
    committee_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    chamber: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # "Referred To", "Markup By", "Reported By" -- and when. The date is what
    # makes this a dated event rather than a standing overlap.
    activity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    activity_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    bill: Mapped["Bill"] = relationship("Bill", back_populates="committees")

    # The date is part of the key, not decoration: a committee can be
    # "Referred to" a bill in one session and again in the next. Two activities
    # of the same name on the SAME day (Natural Resources logged "Unknown"
    # twice, seven minutes apart, on H.R. 1 of the 118th) collapse into one
    # here, which is correct -- a detector working in 60-day windows learns
    # nothing from the seven minutes.
    __table_args__ = (
        Index(
            "uq_bill_committee",
            "bill_id",
            "committee_id",
            "activity",
            "activity_date",
            unique=True,
        ),
    )


class CompanyIndustry(Base):
    """The industry code SEC has assigned an issuer.

    Exists to lift the ceiling on every detector that joins a trade to a
    committee remit or a bill's policy area. Those all ask what sector a holding
    belongs to, and the answer came from a hand-written list of about 70
    large-cap tickers -- so a member trading a mid-cap defense supplier was
    invisible to all of them.

    `sic` is SEC's own Standard Industrial Classification for the filer, read
    from EDGAR. `sector` is this project's reading of it, via
    `sectors.sector_for_sic`, cached here so a detector run is a dict lookup
    rather than 440 prefix comparisons per trade. It is nullable: most SIC codes
    describe industries no committee oversees, and storing the miss is what
    stops the next run paying for the same lookup again.
    """

    __tablename__ = "company_industries"

    id: Mapped[int] = mapped_column(primary_key=True)

    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    cik: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    sic: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    sic_description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Null means "looked up, belongs to no sector we track" -- distinct from a
    # ticker absent from this table, which means "never looked up".
    sector: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<CompanyIndustry {self.ticker} sic={self.sic} sector={self.sector}>"
