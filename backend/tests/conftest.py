"""Shared test fixtures.

Database tests run against a real PostgreSQL database named ``*_test``,
configured via TEST_DATABASE_URL (environment or backend/.env). The schema is
dropped and rebuilt with Alembic once per session, so the URL must never point
at a database with data you care about; the ``_test`` suffix is enforced.
Without TEST_DATABASE_URL those tests are skipped and reported as such.

No test makes network calls: providers are fakes or use mocked HTTP transports.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.container import Container
from app.core.config import Settings
from app.db.models import Base
from app.db.session import create_session_factory
from app.integrations.auth import AuthError, VerifiedIdentity
from app.integrations.storage import MemoryStorage, UrlSigner
from app.main import create_app
from app.providers.image.fake import FakeImageProvider
from app.providers.moderation.fake import FakeModerationProvider

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _read_env_file(name: str) -> str | None:
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].split(" #", 1)[0].strip() or None
    return None


def _test_database_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL") or _read_env_file("TEST_DATABASE_URL")
    if url and not (make_url(url).database or "").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point at a database whose name ends in _test")
    return url


TEST_DATABASE_URL = _test_database_url()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not configured (PostgreSQL *_test database)")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


class MutableClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class FakeTokenVerifier:
    """Tokens look like ``user:<uid>``, ``anon:<uid>`` or anything else (invalid)."""

    async def verify(self, token: str) -> VerifiedIdentity:
        kind, _, uid = token.partition(":")
        if kind == "user" and uid:
            return VerifiedIdentity(uid, f"{uid}@example.com", True, "password")
        if kind == "anon" and uid:
            return VerifiedIdentity(uid, None, False, "anonymous")
        raise AuthError("token_invalid")


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "database_url": TEST_DATABASE_URL or "postgresql+asyncpg://unused@localhost/unused_test",
        "storage_backend": "memory",
        "image_provider": "fake",
        "moderation_provider": "fake",
        "retry_base_delay_seconds": 1.0,
        "retry_max_delay_seconds": 4.0,
        "global_daily_cost_cap_usd": 100.0,
        "global_monthly_cost_cap_usd": 1000.0,
        "user_daily_cost_cap_usd": 50.0,
        "user_daily_generation_cap": 100,
        "user_generations_per_minute": 100,
        "user_generations_per_hour": 1000,
        "welcome_credits": 10,
        "daily_free_credits": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not configured")

    async def reset_schema() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset_schema())
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 10, 18, 12, 0, tzinfo=UTC))


@pytest.fixture
async def container(
    migrated_database: str, clock: MutableClock, request: pytest.FixtureRequest
) -> AsyncIterator[Container]:
    overrides: dict[str, Any] = getattr(request, "param", {})
    settings = make_settings(**overrides)
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    signer = UrlSigner("http://test", b"test-signing-secret")
    c = Container(
        settings=settings,
        engine=engine,
        sessions=create_session_factory(engine),
        storage=MemoryStorage(signer),
        image_provider=FakeImageProvider(),
        moderation=FakeModerationProvider(),
        token_verifier=FakeTokenVerifier(),
        url_signer=signer,
        clock=clock,
    )
    yield c
    await engine.dispose()


@pytest.fixture
async def client(container: Container) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(container)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def auth(uid: str = "alice") -> dict[str, str]:
    return {"Authorization": f"Bearer user:{uid}"}
