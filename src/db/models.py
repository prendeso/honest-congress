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

    # Parsing status
    parsed: Mapped[bool] = mapped_column(default=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)

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
