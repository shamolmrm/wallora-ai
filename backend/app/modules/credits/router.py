from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from google.cloud.firestore_v1 import AsyncTransaction
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel

from app.api.deps import ContainerDep, CurrentUser
from app.db.documents import LedgerDoc, UserDoc
from app.db.firestore import CREDIT_LEDGER, USERS, query_all, run_transaction, tx_get
from app.modules.credits.service import (
    BalanceView,
    CreditBook,
    next_utc_midnight,
)

router = APIRouter(prefix="/v1/me/credits", tags=["credits"])


class LotOut(BaseModel):
    source: str
    remaining: int
    expires_at: datetime | None


class CreditsOut(BaseModel):
    available: int
    held: int
    lots: list[LotOut]
    daily_free_resets_at: datetime


class LedgerEntryOut(BaseModel):
    seq: int
    entry_type: str
    amount: int
    available_after: int
    reason: str | None
    created_at: datetime


class LedgerPage(BaseModel):
    items: list[LedgerEntryOut]
    next_cursor: int | None


@router.get("", response_model=CreditsOut)
async def get_credits(user: CurrentUser, container: ContainerDep) -> CreditsOut:
    s = container.settings
    db = container.db

    async def txn_fn(txn: AsyncTransaction) -> BalanceView:
        now = container.clock()
        user_ref = db.collection(USERS).document(user.id)
        current = UserDoc.from_doc((await tx_get(txn, user_ref)).to_dict() or {})
        book = CreditBook(db, current, now)
        seq_before = current.ledger_seq
        book.ensure_starter_grants(
            welcome_credits=s.welcome_credits, daily_free_credits=s.daily_free_credits
        )
        if current.ledger_seq != seq_before:  # only write when a grant actually happened
            book.write(txn)
            txn.set(user_ref, current.to_doc())
        return book.view()

    view = await run_transaction(db, txn_fn)
    return CreditsOut(
        available=view.available,
        held=view.held,
        lots=[
            LotOut(source=lot.source, remaining=lot.remaining, expires_at=lot.expires_at)
            for lot in view.lots
        ],
        daily_free_resets_at=next_utc_midnight(container.clock()),
    )


@router.get("/ledger", response_model=LedgerPage)
async def get_ledger(
    user: CurrentUser,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: int | None = None,
) -> LedgerPage:
    ledger = container.db.collection(USERS).document(user.id).collection(CREDIT_LEDGER)
    query = ledger.order_by("seq", direction="DESCENDING")
    if cursor is not None:
        query = query.where(filter=FieldFilter("seq", "<", cursor))
    snaps = await query_all(query.limit(limit))
    rows = [LedgerDoc.from_doc(snap.to_dict() or {}) for snap in snaps]
    return LedgerPage(
        items=[
            LedgerEntryOut(
                seq=r.seq,
                entry_type=r.entry_type,
                amount=r.amount,
                available_after=r.available_after,
                reason=r.reason,
                created_at=r.created_at,
            )
            for r in rows
        ],
        next_cursor=rows[-1].seq if len(rows) == limit else None,
    )
