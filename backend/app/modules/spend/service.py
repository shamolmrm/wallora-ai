"""Spend caps enforced with atomic counters.

``commit_estimate`` adds a job's estimated cost to the global day/month and
per-user day counters in the caller's transaction, and raises if any cap
would be exceeded (the transaction then rolls back the increments). The
upsert holds a row lock until commit, so concurrent reservations are
serialised per counter and can never jointly overshoot a cap.

When a job settles, ``adjust`` corrects the same counters (keyed by the job's
creation time) by ``actual - estimate``.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.spend.models import SpendCounter

GLOBAL_ID = "all"


@dataclass(frozen=True)
class SpendLimits:
    global_daily_micros: int
    global_monthly_micros: int
    user_daily_micros: int
    user_daily_jobs: int


class SpendCapError(Exception):
    def __init__(self, scope: str, kind: str) -> None:
        super().__init__(f"{scope} {kind} cap reached")
        self.scope = scope
        self.kind = kind


def usd_to_micros(usd: float) -> int:
    return round(usd * 1_000_000)


def _periods(at: datetime) -> tuple[date, date]:
    day = at.astimezone(UTC).date()
    return day, day.replace(day=1)


async def _add(
    session: AsyncSession,
    *,
    scope: str,
    scope_id: str,
    period: str,
    period_start: date,
    cost_delta: int,
    job_delta: int,
) -> tuple[int, int]:
    stmt = (
        pg_insert(SpendCounter)
        .values(
            scope=scope,
            scope_id=scope_id,
            period=period,
            period_start=period_start,
            cost_usd_micros=cost_delta,
            job_count=job_delta,
        )
        .on_conflict_do_update(
            index_elements=["scope", "scope_id", "period", "period_start"],
            set_={
                "cost_usd_micros": SpendCounter.cost_usd_micros + cost_delta,
                "job_count": SpendCounter.job_count + job_delta,
            },
        )
        .returning(SpendCounter.cost_usd_micros, SpendCounter.job_count)
    )
    cost, jobs = (await session.execute(stmt)).one()
    return int(cost), int(jobs)


async def commit_estimate(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    estimate_micros: int,
    limits: SpendLimits,
    at: datetime,
) -> None:
    day, month = _periods(at)
    cost, _ = await _add(
        session,
        scope="global",
        scope_id=GLOBAL_ID,
        period="day",
        period_start=day,
        cost_delta=estimate_micros,
        job_delta=1,
    )
    if cost > limits.global_daily_micros:
        raise SpendCapError("global", "daily_cost")
    cost, _ = await _add(
        session,
        scope="global",
        scope_id=GLOBAL_ID,
        period="month",
        period_start=month,
        cost_delta=estimate_micros,
        job_delta=1,
    )
    if cost > limits.global_monthly_micros:
        raise SpendCapError("global", "monthly_cost")
    cost, jobs = await _add(
        session,
        scope="user",
        scope_id=str(user_id),
        period="day",
        period_start=day,
        cost_delta=estimate_micros,
        job_delta=1,
    )
    if jobs > limits.user_daily_jobs:
        raise SpendCapError("user", "daily_jobs")
    if cost > limits.user_daily_micros:
        raise SpendCapError("user", "daily_cost")


async def adjust(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cost_delta: int,
    job_delta: int,
    committed_at: datetime,
) -> None:
    if cost_delta == 0 and job_delta == 0:
        return
    day, month = _periods(committed_at)
    for scope, scope_id, period, start in (
        ("global", GLOBAL_ID, "day", day),
        ("global", GLOBAL_ID, "month", month),
        ("user", str(user_id), "day", day),
    ):
        await _add(
            session,
            scope=scope,
            scope_id=scope_id,
            period=period,
            period_start=start,
            cost_delta=cost_delta,
            job_delta=job_delta,
        )
