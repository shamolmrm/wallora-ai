"""Atomic credit operations.

Every function expects to run inside a caller-owned transaction and starts by
locking the user's ``credit_accounts`` row. Lock order is always
account -> lots/reservations -> jobs, which rules out deadlocks between
concurrent requests for the same user.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.modules.credits.models import (
    CreditAccount,
    CreditLedgerEntry,
    CreditLot,
    CreditReservation,
    CreditReservationAllocation,
    LedgerEntryType,
    LotSource,
    ReservationStatus,
)

REFUND_LOT_TTL = timedelta(hours=24)


class InsufficientCreditsError(Exception):
    def __init__(self, required: int, available: int) -> None:
        super().__init__(f"required {required}, available {available}")
        self.required = required
        self.available = available


@dataclass(frozen=True)
class LotView:
    source: str
    remaining: int
    expires_at: datetime | None


@dataclass(frozen=True)
class BalanceView:
    available: int
    held: int
    lots: list[LotView]


def next_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date() + timedelta(days=1), time(0), UTC)


def _spendable(user_id: uuid.UUID, now: datetime) -> tuple[ColumnElement[bool], ...]:
    return (
        CreditLot.user_id == user_id,
        CreditLot.amount_remaining > 0,
        or_(CreditLot.expires_at.is_(None), CreditLot.expires_at > now),
    )


async def lock_account(session: AsyncSession, user_id: uuid.UUID) -> CreditAccount:
    await session.execute(
        pg_insert(CreditAccount).values(user_id=user_id, held=0).on_conflict_do_nothing()
    )
    stmt = (
        select(CreditAccount)
        .where(CreditAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def available_balance(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
    stmt = select(func.coalesce(func.sum(CreditLot.amount_remaining), 0)).where(
        *_spendable(user_id, now)
    )
    return int((await session.execute(stmt)).scalar_one())


async def _append_ledger(
    session: AsyncSession,
    *,
    account: CreditAccount,
    entry_type: LedgerEntryType,
    amount: int,
    held_delta: int,
    now: datetime,
    lot_id: uuid.UUID | None = None,
    reservation_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> None:
    await session.flush()
    available = await available_balance(session, account.user_id, now)
    session.add(
        CreditLedgerEntry(
            id=new_id(),
            user_id=account.user_id,
            entry_type=entry_type,
            amount=amount,
            held_delta=held_delta,
            available_after=available,
            held_after=account.held,
            lot_id=lot_id,
            reservation_id=reservation_id,
            job_id=job_id,
            reason=reason,
            created_at=now,
        )
    )
    await session.flush()


async def grant(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    source: LotSource,
    source_ref: str,
    amount: int,
    now: datetime,
    expires_at: datetime | None = None,
    reason: str | None = None,
) -> bool:
    """Grant credits once per (user, source, source_ref). Returns False if already granted."""
    if amount <= 0:
        return False
    account = await lock_account(session, user_id)
    lot_id = new_id()
    stmt = (
        pg_insert(CreditLot)
        .values(
            id=lot_id,
            user_id=user_id,
            source=source,
            source_ref=source_ref,
            amount_granted=amount,
            amount_remaining=amount,
            expires_at=expires_at,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "source", "source_ref"])
        .returning(CreditLot.id)
    )
    if (await session.execute(stmt)).scalar_one_or_none() is None:
        return False
    await _append_ledger(
        session,
        account=account,
        entry_type=LedgerEntryType.GRANT,
        amount=amount,
        held_delta=0,
        now=now,
        lot_id=lot_id,
        reason=reason or source,
    )
    return True


async def ensure_starter_grants(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime,
    welcome_credits: int,
    daily_free_credits: int,
) -> None:
    """Idempotently grant the one-off welcome lot and today's (UTC) free lot."""
    await grant(
        session,
        user_id=user_id,
        source=LotSource.WELCOME,
        source_ref="welcome",
        amount=welcome_credits,
        now=now,
    )
    await grant(
        session,
        user_id=user_id,
        source=LotSource.DAILY_FREE,
        source_ref=now.astimezone(UTC).date().isoformat(),
        amount=daily_free_credits,
        now=now,
        expires_at=next_utc_midnight(now),
    )


async def reserve(
    session: AsyncSession, *, user_id: uuid.UUID, job_id: uuid.UUID, amount: int, now: datetime
) -> CreditReservation:
    account = await lock_account(session, user_id)
    lots_stmt = (
        select(CreditLot)
        .where(*_spendable(user_id, now))
        .order_by(CreditLot.expires_at.asc().nulls_last(), CreditLot.created_at, CreditLot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    lots = list((await session.execute(lots_stmt)).scalars())
    available = sum(lot.amount_remaining for lot in lots)
    if available < amount:
        raise InsufficientCreditsError(amount, available)

    reservation = CreditReservation(
        id=new_id(),
        user_id=user_id,
        job_id=job_id,
        amount=amount,
        status=ReservationStatus.HELD,
        created_at=now,
    )
    session.add(reservation)
    await session.flush()
    still_needed = amount
    for lot in lots:
        take = min(lot.amount_remaining, still_needed)
        lot.amount_remaining -= take
        session.add(
            CreditReservationAllocation(reservation_id=reservation.id, lot_id=lot.id, amount=take)
        )
        still_needed -= take
        if still_needed == 0:
            break
    account.held += amount
    await _append_ledger(
        session,
        account=account,
        entry_type=LedgerEntryType.RESERVE,
        amount=-amount,
        held_delta=amount,
        now=now,
        reservation_id=reservation.id,
        job_id=job_id,
    )
    return reservation


async def _lock_reservation(
    session: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID
) -> tuple[CreditAccount, CreditReservation | None]:
    account = await lock_account(session, user_id)
    stmt = (
        select(CreditReservation)
        .where(CreditReservation.job_id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return account, (await session.execute(stmt)).scalar_one_or_none()


async def capture(
    session: AsyncSession, *, user_id: uuid.UUID, job_id: uuid.UUID, now: datetime
) -> bool:
    """Consume held credits after a successful job. Idempotent."""
    account, reservation = await _lock_reservation(session, user_id, job_id)
    if reservation is None or reservation.status != ReservationStatus.HELD:
        return False
    reservation.status = ReservationStatus.CAPTURED
    reservation.resolved_at = now
    account.held -= reservation.amount
    await _append_ledger(
        session,
        account=account,
        entry_type=LedgerEntryType.CAPTURE,
        amount=0,
        held_delta=-reservation.amount,
        now=now,
        reservation_id=reservation.id,
        job_id=job_id,
    )
    return True


async def release(
    session: AsyncSession, *, user_id: uuid.UUID, job_id: uuid.UUID, now: datetime, reason: str
) -> bool:
    """Return held credits to their lots (or a 24h refund lot if a lot expired). Idempotent."""
    account, reservation = await _lock_reservation(session, user_id, job_id)
    if reservation is None or reservation.status != ReservationStatus.HELD:
        return False
    alloc_stmt = (
        select(CreditReservationAllocation, CreditLot)
        .join(CreditLot, CreditLot.id == CreditReservationAllocation.lot_id)
        .where(CreditReservationAllocation.reservation_id == reservation.id)
        .order_by(CreditLot.id)
        .with_for_update(of=CreditLot)
        .execution_options(populate_existing=True)
    )
    expired_total = 0
    for allocation, lot in await session.execute(alloc_stmt):
        if lot.expires_at is not None and lot.expires_at <= now:
            expired_total += allocation.amount
        else:
            lot.amount_remaining += allocation.amount
    if expired_total:
        session.add(
            CreditLot(
                id=new_id(),
                user_id=user_id,
                source=LotSource.REFUND,
                source_ref=str(reservation.id),
                amount_granted=expired_total,
                amount_remaining=expired_total,
                expires_at=now + REFUND_LOT_TTL,
                created_at=now,
            )
        )
    reservation.status = ReservationStatus.RELEASED
    reservation.resolved_at = now
    account.held -= reservation.amount
    await _append_ledger(
        session,
        account=account,
        entry_type=LedgerEntryType.RELEASE,
        amount=reservation.amount,
        held_delta=-reservation.amount,
        now=now,
        reservation_id=reservation.id,
        job_id=job_id,
        reason=reason,
    )
    return True


async def balance(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> BalanceView:
    lots_stmt = (
        select(CreditLot)
        .where(*_spendable(user_id, now))
        .order_by(CreditLot.expires_at.asc().nulls_last(), CreditLot.created_at)
    )
    lots = list((await session.execute(lots_stmt)).scalars())
    held = await session.scalar(select(CreditAccount.held).where(CreditAccount.user_id == user_id))
    return BalanceView(
        available=sum(lot.amount_remaining for lot in lots),
        held=held or 0,
        lots=[LotView(lot.source, lot.amount_remaining, lot.expires_at) for lot in lots],
    )
