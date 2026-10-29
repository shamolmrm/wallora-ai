"""CreditBook behaviour against the Firestore emulator."""

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
from google.api_core.exceptions import Conflict
from google.cloud.firestore_v1 import AsyncTransaction

from app.container import Container
from app.core.ids import new_id
from app.db.documents import LotSource, ReservationDoc, UserDoc, UserStatus
from app.db.firestore import CREDIT_LEDGER, USERS, run_transaction, tx_get
from app.modules.credits.service import (
    BalanceView,
    CreditBook,
    InsufficientCreditsError,
)
from tests.conftest import MutableClock

pytestmark = pytest.mark.firestore


async def _user(c: Container) -> UserDoc:
    now = c.clock()
    user = UserDoc(
        id=str(new_id()),
        firebase_uid=f"uid-{new_id()}",
        email=None,
        email_verified=False,
        status=UserStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    await c.db.collection(USERS).document(user.id).set(user.to_doc())
    return user


async def _with_book(
    c: Container,
    user_id: str,
    action: Callable[[CreditBook], object],
    *,
    also: list[str] | None = None,
) -> BalanceView:
    """Run ``action(book)`` in a transaction and persist the result."""

    async def txn_fn(txn: AsyncTransaction) -> BalanceView:
        ref = c.db.collection(USERS).document(user_id)
        user = UserDoc.from_doc((await tx_get(txn, ref)).to_dict() or {})
        book = CreditBook(c.db, user, c.clock())
        action(book)
        book.write(txn)
        txn.set(ref, user.to_doc())
        return book.view()

    return await run_transaction(c.db, txn_fn)


async def _lots(c: Container, user_id: str) -> dict[str, int]:
    snap = await c.db.collection(USERS).document(user_id).get()
    lots = (snap.to_dict() or {}).get("credit_lots", {})
    return {lot["source"]: lot["amount_remaining"] for lot in lots.values()}


async def test_grant_is_idempotent(container: Container) -> None:
    user = await _user(container)
    first = await _with_book(container, user.id, lambda b: b.grant(LotSource.ADMIN, "a1", 5))
    second = await _with_book(container, user.id, lambda b: b.grant(LotSource.ADMIN, "a1", 5))
    assert (first.available, second.available) == (5, 5)


async def test_spent_welcome_lot_is_not_granted_again(container: Container) -> None:
    user = await _user(container)

    def grant_and_spend(book: CreditBook) -> None:
        book.ensure_starter_grants(welcome_credits=3, daily_free_credits=0)
        book.reserve("job-1", 3)

    await _with_book(container, user.id, grant_and_spend)
    view = await _with_book(
        container,
        user.id,
        lambda b: b.ensure_starter_grants(welcome_credits=3, daily_free_credits=0),
    )
    assert view.available == 0  # the spent lot still exists, so no second welcome grant


async def test_reserve_spends_soonest_expiring_first(container: Container) -> None:
    user = await _user(container)

    def grant_and_reserve(book: CreditBook) -> None:
        book.ensure_starter_grants(welcome_credits=5, daily_free_credits=2)
        book.reserve("job-1", 3)

    await _with_book(container, user.id, grant_and_reserve)
    # The expiring daily lot (2) is used first, then 1 from the non-expiring welcome lot.
    assert await _lots(container, user.id) == {"daily_free": 0, "welcome": 4}


async def test_release_after_lot_expiry_creates_refund_lot(
    container: Container, clock: MutableClock
) -> None:
    user = await _user(container)
    holder: dict[str, ReservationDoc] = {}

    def grant_and_reserve(book: CreditBook) -> None:
        book.ensure_starter_grants(welcome_credits=0, daily_free_credits=3)
        holder["reservation"] = book.reserve("job-1", 3)

    await _with_book(container, user.id, grant_and_reserve)
    reservation = holder["reservation"]
    clock.advance(days=1)  # the daily lot has expired by the time the job fails
    view = await _with_book(
        container,
        user.id,
        lambda b: b.release(reservation, "job-1", "failed"),
    )
    assert (view.available, view.held) == (3, 0)
    assert [lot.source for lot in view.lots] == ["refund"]
    assert view.lots[0].expires_at == clock() + timedelta(hours=24)


async def test_capture_and_release_are_idempotent_and_exclusive(container: Container) -> None:
    user = await _user(container)
    holder: dict[str, ReservationDoc] = {}

    def grant_and_reserve(book: CreditBook) -> None:
        book.ensure_starter_grants(welcome_credits=5, daily_free_credits=0)
        holder["reservation"] = book.reserve("job-1", 3)

    await _with_book(container, user.id, grant_and_reserve)
    reservation = holder["reservation"]
    results: list[bool] = []

    def settle(book: CreditBook) -> None:
        results.append(book.capture(reservation, "job-1"))
        results.append(book.capture(reservation, "job-1"))
        results.append(book.release(reservation, "job-1", "x"))

    view = await _with_book(container, user.id, settle)
    assert results == [True, False, False]
    assert (view.available, view.held) == (2, 0)


async def test_insufficient_credits_raises_without_writes(container: Container) -> None:
    user = await _user(container)
    with pytest.raises(InsufficientCreditsError):
        await _with_book(container, user.id, lambda b: b.reserve("job-1", 1))
    ledger = container.db.collection(USERS).document(user.id).collection(CREDIT_LEDGER)
    assert [snap async for snap in ledger.stream()] == []


async def test_ledger_entries_are_never_overwritten(container: Container) -> None:
    user = await _user(container)
    await _with_book(
        container,
        user.id,
        lambda b: b.grant(LotSource.ADMIN, "x", 1),
    )
    entry = (
        container.db.collection(USERS)
        .document(user.id)
        .collection(CREDIT_LEDGER)
        .document(f"{1:012d}")
    )
    with pytest.raises(Conflict):
        await entry.create({"amount": 1000})


async def test_parallel_reservations_never_overspend(container: Container) -> None:
    user = await _user(container)
    await _with_book(
        container,
        user.id,
        lambda b: b.ensure_starter_grants(welcome_credits=10, daily_free_credits=0),
    )

    async def attempt(n: int) -> bool:
        try:
            await _with_book(container, user.id, lambda b: b.reserve(f"job-{n}", 3))
        except InsufficientCreditsError:
            return False
        return True

    # 5 concurrent requests: the Firestore emulator can hold locks for up to 30 s under
    # contention (documented emulator limitation), so higher fan-out only slows CI down.
    results = await asyncio.gather(*(attempt(n) for n in range(5)))

    assert sum(results) == 3
    view = await _with_book(container, user.id, lambda b: None)
    assert (view.available, view.held) == (1, 9)
    ledger = container.db.collection(USERS).document(user.id).collection(CREDIT_LEDGER)
    entries = [snap.to_dict() or {} async for snap in ledger.stream()]
    # Ledger invariant: grants minus reservations equals the available balance,
    # and sequence numbers are gap-free.
    assert sum(e["amount"] for e in entries) == view.available
    assert sorted(e["seq"] for e in entries) == list(range(1, len(entries) + 1))
