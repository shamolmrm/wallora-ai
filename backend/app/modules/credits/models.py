"""Credit storage.

* ``credit_lots`` hold granted credits with optional expiry. Spending consumes
  lots soonest-expiring first.
* ``credit_reservations`` hold credits for one generation job until it is
  captured (success) or released (failure/cancel).
* ``credit_ledger`` is the append-only audit trail; a database trigger rejects
  UPDATE and DELETE.
* ``credit_accounts`` is the per-user lock target that serialises every credit
  mutation, plus the cached ``held`` total.

Available balance is always computed live as the sum of unexpired lot
remainders, so expiry never leaves a stale cached balance.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base, utcnow


class LotSource(StrEnum):
    WELCOME = "welcome"
    DAILY_FREE = "daily_free"
    REWARD_AD = "reward_ad"
    PURCHASE = "purchase"
    SUBSCRIPTION = "subscription"
    ADMIN = "admin"
    REFUND = "refund"


class ReservationStatus(StrEnum):
    HELD = "held"
    CAPTURED = "captured"
    RELEASED = "released"


class LedgerEntryType(StrEnum):
    GRANT = "grant"
    RESERVE = "reserve"
    CAPTURE = "capture"
    RELEASE = "release"
    EXPIRE = "expire"
    REVOKE = "revoke"
    ADMIN_ADJUST = "admin_adjust"


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    __table_args__ = (CheckConstraint("held >= 0", name="held_non_negative"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    held: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=utcnow, onupdate=utcnow
    )


class CreditLot(Base):
    __tablename__ = "credit_lots"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_ref"),
        CheckConstraint("amount_granted > 0", name="granted_positive"),
        CheckConstraint(
            "amount_remaining >= 0 AND amount_remaining <= amount_granted", name="remaining_range"
        ),
        CheckConstraint(
            "source IN ('welcome', 'daily_free', 'reward_ad', 'purchase', 'subscription', "
            "'admin', 'refund')",
            name="source",
        ),
        Index(
            "ix_credit_lots_spendable",
            "user_id",
            "expires_at",
            postgresql_where=text("amount_remaining > 0"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(200))
    amount_granted: Mapped[int] = mapped_column(Integer)
    amount_remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)


class CreditReservation(Base):
    __tablename__ = "credit_reservations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('held', 'captured', 'released')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), unique=True
    )
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=ReservationStatus.HELD)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    resolved_at: Mapped[datetime | None]


class CreditReservationAllocation(Base):
    __tablename__ = "credit_reservation_allocations"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credit_reservations.id", ondelete="CASCADE"), primary_key=True
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credit_lots.id", ondelete="CASCADE"), primary_key=True
    )
    amount: Mapped[int] = mapped_column(Integer)


class CreditLedgerEntry(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('grant', 'reserve', 'capture', 'release', 'expire', 'revoke', "
            "'admin_adjust')",
            name="entry_type",
        ),
        Index("ix_credit_ledger_user_seq", "user_id", "seq"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(unique=True, default=new_id)
    # No foreign keys: ledger rows must outlive (pseudonymised) users and deleted jobs.
    user_id: Mapped[uuid.UUID]
    entry_type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(Integer)  # delta on available credits
    held_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_after: Mapped[int] = mapped_column(Integer)
    held_after: Mapped[int] = mapped_column(Integer)
    lot_id: Mapped[uuid.UUID | None]
    reservation_id: Mapped[uuid.UUID | None]
    job_id: Mapped[uuid.UUID | None]
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
