import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.container import Container
from app.core.ids import new_id
from app.db.models import Base
from app.modules.credits import service as credits
from app.modules.credits.models import CreditLedgerEntry, CreditLot, LotSource
from app.modules.studio.models import GenerationJob, JobStatus
from app.modules.users.models import User
from tests.conftest import BACKEND_DIR, MutableClock

pytestmark = pytest.mark.postgres


async def _user(c: Container) -> User:
    async with c.sessions() as s, s.begin():
        user = User(id=new_id(), firebase_uid=f"uid-{new_id()}", email=None)
        s.add(user)
    return user


async def _job(c: Container, user: User) -> GenerationJob:
    now = c.clock()
    async with c.sessions() as s, s.begin():
        job = GenerationJob(
            id=new_id(),
            user_id=user.id,
            idempotency_key=str(new_id()),
            request_hash="h",
            kind="generate",
            status=JobStatus.QUEUED,
            prompt="p",
            tier="standard",
            aspect="phone",
            width=1072,
            height=2320,
            quality="medium",
            output_format="webp",
            provider="fake",
            model="gpt-image-2.5-flare",
            credit_cost=3,
            estimated_cost_usd_micros=1000,
            max_attempts=3,
            created_at=now,
            updated_at=now,
        )
        s.add(job)
    return job


async def test_models_match_migration(container: Container) -> None:
    def diff(conn: object) -> list[object]:
        context = MigrationContext.configure(conn)  # type: ignore[arg-type]
        return list(compare_metadata(context, Base.metadata))

    async with container.engine.connect() as conn:
        differences = await conn.run_sync(diff)
    assert differences == []


def test_downgrade_and_upgrade_round_trip(migrated_database: str) -> None:
    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.attributes["database_url"] = migrated_database
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def test_grant_is_idempotent(container: Container) -> None:
    user = await _user(container)
    now = container.clock()
    async with container.sessions() as s, s.begin():
        first = await credits.grant(
            s, user_id=user.id, source=LotSource.ADMIN, source_ref="a1", amount=5, now=now
        )
        second = await credits.grant(
            s, user_id=user.id, source=LotSource.ADMIN, source_ref="a1", amount=5, now=now
        )
        assert (first, second) == (True, False)
        assert await credits.available_balance(s, user.id, now) == 5


async def test_reserve_spends_soonest_expiring_first(container: Container) -> None:
    user = await _user(container)
    job = await _job(container, user)
    now = container.clock()
    async with container.sessions() as s, s.begin():
        await credits.ensure_starter_grants(
            s, user_id=user.id, now=now, welcome_credits=5, daily_free_credits=2
        )
        await credits.reserve(s, user_id=user.id, job_id=job.id, amount=3, now=now)
        lots = {
            lot.source: lot.amount_remaining
            for lot in (
                await s.execute(select(CreditLot).where(CreditLot.user_id == user.id))
            ).scalars()
        }
    # The expiring daily lot (2) is used up first, then 1 from the non-expiring welcome lot.
    assert lots == {"daily_free": 0, "welcome": 4}


async def test_release_after_lot_expiry_creates_refund_lot(
    container: Container, clock: MutableClock
) -> None:
    user = await _user(container)
    job = await _job(container, user)
    async with container.sessions() as s, s.begin():
        await credits.ensure_starter_grants(
            s, user_id=user.id, now=clock(), welcome_credits=0, daily_free_credits=3
        )
        await credits.reserve(s, user_id=user.id, job_id=job.id, amount=3, now=clock())
    clock.advance(days=1)  # the daily lot has expired by the time the job fails
    async with container.sessions() as s, s.begin():
        assert await credits.release(
            s, user_id=user.id, job_id=job.id, now=clock(), reason="failed"
        )
        view = await credits.balance(s, user.id, clock())
    assert view.available == 3
    assert view.held == 0
    assert [lot.source for lot in view.lots] == ["refund"]
    assert view.lots[0].expires_at == clock() + timedelta(hours=24)


async def test_capture_and_release_are_idempotent_and_exclusive(container: Container) -> None:
    user = await _user(container)
    job = await _job(container, user)
    now = container.clock()
    async with container.sessions() as s, s.begin():
        await credits.ensure_starter_grants(
            s, user_id=user.id, now=now, welcome_credits=5, daily_free_credits=0
        )
        await credits.reserve(s, user_id=user.id, job_id=job.id, amount=3, now=now)
    async with container.sessions() as s, s.begin():
        assert await credits.capture(s, user_id=user.id, job_id=job.id, now=now)
        assert not await credits.capture(s, user_id=user.id, job_id=job.id, now=now)
        assert not await credits.release(s, user_id=user.id, job_id=job.id, now=now, reason="x")
        view = await credits.balance(s, user.id, now)
    assert (view.available, view.held) == (2, 0)


async def test_ledger_is_append_only(container: Container) -> None:
    user = await _user(container)
    async with container.sessions() as s, s.begin():
        await credits.grant(
            s,
            user_id=user.id,
            source=LotSource.ADMIN,
            source_ref="x",
            amount=1,
            now=container.clock(),
        )
    for statement in ("UPDATE credit_ledger SET amount = 100", "DELETE FROM credit_ledger"):
        async with container.engine.connect() as conn:
            with pytest.raises(DBAPIError, match="append-only"):
                await conn.execute(text(statement))


async def test_parallel_reservations_never_overspend(container: Container) -> None:
    user = await _user(container)
    jobs = [await _job(container, user) for _ in range(12)]
    async with container.sessions() as s, s.begin():
        await credits.ensure_starter_grants(
            s, user_id=user.id, now=container.clock(), welcome_credits=10, daily_free_credits=0
        )

    async def attempt(job: GenerationJob) -> bool:
        try:
            async with container.sessions() as s, s.begin():
                await credits.reserve(
                    s, user_id=user.id, job_id=job.id, amount=3, now=container.clock()
                )
        except credits.InsufficientCreditsError:
            return False
        return True

    results = await asyncio.gather(*(attempt(job) for job in jobs))

    assert sum(results) == 3
    async with container.sessions() as s:
        view = await credits.balance(s, user.id, container.clock())
        ledger_total = await s.scalar(
            select(func.sum(CreditLedgerEntry.amount)).where(CreditLedgerEntry.user_id == user.id)
        )
    assert (view.available, view.held) == (1, 9)
    # Ledger invariant: grants minus reservations equals the available balance.
    assert ledger_total == view.available
