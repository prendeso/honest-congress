from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text, 
    Numeric, Enum as SQLEnum, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship, DeclarativeBase, Mapped, mapped_column
from enum import Enum


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
    district: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    
    # Current status
    in_office: Mapped[bool] = mapped_column(default=True)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
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
    document_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # PTR (Periodic Transaction Report) flag
    is_ptr: Mapped[bool] = mapped_column(default=False, index=True)

    # Parsing status
    parsed: Mapped[bool] = mapped_column(default=False)
    parse_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
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
    ticker: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    
    # Value range
    value_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    value_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    
    # Income from this asset
    income_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    income_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    income_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
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
    ticker: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    
    # Amount range
    amount_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    amount_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    
    # Owner
    owner: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
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
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    liability_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Amount range
    amount_min: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    amount_max: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relationships
    disclosure: Mapped["Disclosure"] = relationship("Disclosure", back_populates="liabilities")
    
    def __repr__(self) -> str:
        return f"<Liability {self.creditor}>"


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
    disclosure_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("disclosures.id"), nullable=True
    )
    transaction_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )
    
    # Computed values
    computed_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    threshold_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    
    # Metadata
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed: Mapped[bool] = mapped_column(default=False)
    
    # Relationships
    member: Mapped["Member"] = relationship("Member", back_populates="anomalies")
    
    def __repr__(self) -> str:
        return f"<Anomaly {self.anomaly_type}: {self.title[:30]}...>"

