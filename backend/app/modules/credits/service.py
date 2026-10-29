"""Atomic credit operations on Firestore.

A ``CreditBook`` wraps a user document read inside a transaction. Lots live on
that document, so every operation runs purely in memory and ``write`` stages
the ledger entries (the caller writes the user document). Reading the user
document locks it for the whole transaction, so concurrent credit changes for
one user are serialised - without any query inside the transaction.

Expired lots are pruned from the document after ``PRUNE_AFTER``; a release
that finds its lot pruned treats it as expired (refund lot), which is exactly
what would have happened had the lot still been there.

Available balance is always the live sum of unexpired lot remainders, so
expiry never leaves a stale cached balance.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from google.cloud.firestore_v1 import AsyncClient, AsyncTransaction

from app.db.documents import (
    LedgerDoc,
    LedgerEntryType,
    LotDoc,
    LotSource,
    ReservationDoc,
    ReservationStatus,
    UserDoc,
)
from app.db.firestore import CREDIT_LEDGER, USERS

REFUND_LOT_TTL = timedelta(hours=24)
PRUNE_AFTER = timedelta(hours=48)


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


def lot_id(source: str, source_ref: str) -> str:
    """Deterministic lot ID: a grant for the same (source, ref) can only exist once."""
    return hashlib.sha256(f"{source}\x1f{source_ref}".encode()).hexdigest()[:40]


def _spendable(lot: LotDoc, now: datetime) -> bool:
    return lot.amount_remaining > 0 and (lot.expires_at is None or lot.expires_at > now)


def _spend_order(lot: LotDoc) -> tuple[bool, datetime, datetime, str]:
    far_future = datetime.max.replace(tzinfo=UTC)
    return (lot.expires_at is None, lot.expires_at or far_future, lot.created_at, lot.id)


class CreditBook:
    def __init__(self, db: AsyncClient, user: UserDoc, now: datetime) -> None:
        self.user = user
        self.now = now
        self._ledger_ref = db.collection(USERS).document(user.id).collection(CREDIT_LEDGER)
        self.lots: dict[str, LotDoc] = {
            lid: LotDoc.from_doc(value) for lid, value in user.credit_lots.items()
        }
        self._ledger: list[LedgerDoc] = []

    # ------------------------------------------------------------------ queries
    @property
    def available(self) -> int:
        return sum(lot.amount_remaining for lot in self.lots.values() if _spendable(lot, self.now))

    def view(self) -> BalanceView:
        lots = sorted(
            (lot for lot in self.lots.values() if _spendable(lot, self.now)), key=_spend_order
        )
        return BalanceView(
            available=sum(lot.amount_remaining for lot in lots),
            held=self.user.held,
            lots=[LotView(lot.source, lot.amount_remaining, lot.expires_at) for lot in lots],
        )

    # ------------------------------------------------------------------ mutations
    def _record(
        self,
        entry_type: LedgerEntryType,
        *,
        amount: int,
        held_delta: int,
        lot: str | None = None,
        job_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.user.ledger_seq += 1
        self._ledger.append(
            LedgerDoc(
                seq=self.user.ledger_seq,
                user_id=self.user.id,
                entry_type=entry_type,
                amount=amount,
                held_delta=held_delta,
                available_after=self.available,
                held_after=self.user.held,
                lot_id=lot,
                job_id=job_id,
                reason=reason,
                created_at=self.now,
            )
        )

    def grant(
        self,
        source: LotSource,
        source_ref: str,
        amount: int,
        *,
        expires_at: datetime | None = None,
        reason: str | None = None,
    ) -> bool:
        """Grant once per (source, source_ref)."""
        lid = lot_id(source, source_ref)
        if amount <= 0 or lid in self.lots:
            return False
        self.lots[lid] = LotDoc(
            id=lid,
            source=source,
            source_ref=source_ref,
            amount_granted=amount,
            amount_remaining=amount,
            expires_at=expires_at,
            created_at=self.now,
        )
        self._record(
            LedgerEntryType.GRANT, amount=amount, held_delta=0, lot=lid, reason=reason or source
        )
        return True

    def ensure_starter_grants(self, *, welcome_credits: int, daily_free_credits: int) -> None:
        self.grant(LotSource.WELCOME, "welcome", welcome_credits)
        self.grant(
            LotSource.DAILY_FREE,
            self.now.astimezone(UTC).date().isoformat(),
            daily_free_credits,
            expires_at=next_utc_midnight(self.now),
        )

    def reserve(self, job_id: str, amount: int) -> ReservationDoc:
        lots = sorted(
            (lot for lot in self.lots.values() if _spendable(lot, self.now)), key=_spend_order
        )
        available = sum(lot.amount_remaining for lot in lots)
        if available < amount:
            raise InsufficientCreditsError(amount, available)
        allocations: list[dict[str, object]] = []
        needed = amount
        for lot in lots:
            take = min(lot.amount_remaining, needed)
            lot.amount_remaining -= take
            allocations.append({"lot_id": lot.id, "amount": take})
            needed -= take
            if needed == 0:
                break
        self.user.held += amount
        self._record(LedgerEntryType.RESERVE, amount=-amount, held_delta=amount, job_id=job_id)
        return ReservationDoc(
            amount=amount,
            status=ReservationStatus.HELD,
            allocations=allocations,
            created_at=self.now,
        )

    def capture(self, reservation: ReservationDoc, job_id: str) -> bool:
        """Consume held credits after success. Idempotent."""
        if reservation.status != ReservationStatus.HELD:
            return False
        reservation.status = ReservationStatus.CAPTURED
        reservation.resolved_at = self.now
        self.user.held -= reservation.amount
        self._record(
            LedgerEntryType.CAPTURE, amount=0, held_delta=-reservation.amount, job_id=job_id
        )
        return True

    def release(self, reservation: ReservationDoc, job_id: str, reason: str) -> bool:
        """Return held credits to their lots, or a 24h refund lot if a lot expired. Idempotent."""
        if reservation.status != ReservationStatus.HELD:
            return False
        expired = 0
        for allocation in reservation.allocation_list():
            lot = self.lots.get(allocation.lot_id)  # None: pruned, which implies expired
            if lot is None or (lot.expires_at is not None and lot.expires_at <= self.now):
                expired += allocation.amount
            else:
                lot.amount_remaining += allocation.amount
        if expired:
            refund = lot_id(LotSource.REFUND, job_id)
            self.lots[refund] = LotDoc(
                id=refund,
                source=LotSource.REFUND,
                source_ref=job_id,
                amount_granted=expired,
                amount_remaining=expired,
                expires_at=self.now + REFUND_LOT_TTL,
                created_at=self.now,
            )
        reservation.status = ReservationStatus.RELEASED
        reservation.resolved_at = self.now
        self.user.held -= reservation.amount
        self._record(
            LedgerEntryType.RELEASE,
            amount=reservation.amount,
            held_delta=-reservation.amount,
            job_id=job_id,
            reason=reason,
        )
        return True

    # ------------------------------------------------------------------ persistence
    def write(self, txn: AsyncTransaction) -> None:
        """Store lots on the user (the caller writes the user document) and stage the ledger."""
        cutoff = self.now - PRUNE_AFTER
        self.user.credit_lots = {
            lid: lot.to_doc()
            for lid, lot in self.lots.items()
            if lot.expires_at is None or lot.expires_at > cutoff
        }
        for entry in self._ledger:
            # create() fails if the sequence number exists: ledger entries are never overwritten.
            txn.create(self._ledger_ref.document(f"{entry.seq:012d}"), entry.to_doc())
