"""Shared test fixtures.

Database tests run against the Firestore emulator, never a real project:

    firebase emulators:exec --only firestore --project demo-wallora "py -m uv run pytest"

``emulators:exec`` sets FIRESTORE_EMULATOR_HOST. Only ``demo-*`` project IDs
are accepted (Firebase's convention for projects with no real backend), and
all emulator data is wiped before each test. Without the emulator those tests
are skipped and reported as such.

No test makes paid or external network calls: providers are fakes or use
mocked HTTP transports.
"""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from google.cloud import firestore

from app.container import Container
from app.core.config import Settings
from app.integrations.auth import AuthError, VerifiedIdentity
from app.integrations.storage import MemoryStorage, UrlSigner
from app.main import create_app
from app.providers.image.fake import FakeImageProvider
from app.providers.moderation.fake import FakeModerationProvider

BACKEND_DIR = Path(__file__).resolve().parent.parent
EMULATOR_HOST = os.environ.get("FIRESTORE_EMULATOR_HOST")
TEST_PROJECT = os.environ.get("GCLOUD_PROJECT", "demo-wallora")

if EMULATOR_HOST and not TEST_PROJECT.startswith("demo-"):
    raise RuntimeError("Firestore tests only run against a demo-* emulator project")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if EMULATOR_HOST:
        return
    skip = pytest.mark.skip(reason="Firestore emulator not running (FIRESTORE_EMULATOR_HOST)")
    for item in items:
        if "firestore" in item.keywords:
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
        "firestore_project_id": TEST_PROJECT,
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


async def _wipe_emulator() -> None:
    url = (
        f"http://{EMULATOR_HOST}/emulator/v1/projects/{TEST_PROJECT}/databases/(default)/documents"
    )
    async with httpx2.AsyncClient() as http:
        response = await http.delete(url)
        response.raise_for_status()


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 10, 18, 12, 0, tzinfo=UTC))


@pytest.fixture
async def container(
    clock: MutableClock, request: pytest.FixtureRequest
) -> AsyncIterator[Container]:
    if not EMULATOR_HOST:
        pytest.skip("Firestore emulator not running")
    await _wipe_emulator()
    overrides: dict[str, Any] = getattr(request, "param", {})
    settings = make_settings(**overrides)
    signer = UrlSigner("http://test", b"test-signing-secret")
    c = Container(
        settings=settings,
        db=firestore.AsyncClient(project=TEST_PROJECT),
        storage=MemoryStorage(signer),
        image_provider=FakeImageProvider(),
        moderation=FakeModerationProvider(),
        token_verifier=FakeTokenVerifier(),
        url_signer=signer,
        clock=clock,
    )
    yield c
    await c.close()


@pytest.fixture
async def client(container: Container) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(container)
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def auth(uid: str = "alice") -> dict[str, str]:
    return {"Authorization": f"Bearer user:{uid}"}
