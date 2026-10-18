from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import ContainerDep, CurrentUser
from app.modules.credits import service as credits
from app.modules.credits.models import CreditLedgerEntry

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
    now = container.clock()
    async with container.sessions() as session, session.begin():
        await credits.ensure_starter_grants(
            session,
            user_id=user.id,
            now=now,
            welcome_credits=s.welcome_credits,
            daily_free_credits=s.daily_free_credits,
        )
        view = await credits.balance(session, user.id, now)
    return CreditsOut(
        available=view.available,
        held=view.held,
        lots=[
            LotOut(source=lot.source, remaining=lot.remaining, expires_at=lot.expires_at)
            for lot in view.lots
        ],
        daily_free_resets_at=credits.next_utc_midnight(now),
    )


@router.get("/ledger", response_model=LedgerPage)
async def get_ledger(
    user: CurrentUser,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: int | None = None,
) -> LedgerPage:
    stmt = select(CreditLedgerEntry).where(CreditLedgerEntry.user_id == user.id)
    if cursor is not None:
        stmt = stmt.where(CreditLedgerEntry.seq < cursor)
    stmt = stmt.order_by(CreditLedgerEntry.seq.desc()).limit(limit)
    async with container.sessions() as session:
        rows = list((await session.execute(stmt)).scalars())
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
