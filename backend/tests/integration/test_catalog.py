"""Catalog API and the budget-guarded generation script, on the Firestore emulator."""

import httpx2
import pytest

from app.container import Container
from app.db.documents import WallpaperStatus
from app.db.firestore import WALLPAPERS
from app.modules.catalog.categories import CATEGORY_SEEDS
from app.modules.catalog.generate import generate_catalog
from app.providers.image.base import ProviderError
from app.providers.image.fake import FakeImageProvider, FakeOutcome
from app.providers.moderation.fake import FakeModerationProvider

pytestmark = pytest.mark.firestore


async def test_empty_catalog(client: httpx2.AsyncClient) -> None:
    data = (await client.get("/v1/discover")).json()
    assert data == {"featured": [], "categories": [], "latest": []}


async def test_generate_then_discover(client: httpx2.AsyncClient, container: Container) -> None:
    report = await generate_catalog(
        container, per_category=2, max_usd=None, confirm_spend=False, dry_run=False
    )
    assert report.generated == 2 * len(CATEGORY_SEEDS)
    assert report.spent_micros == 0  # the fake provider costs nothing

    discover = (await client.get("/v1/discover")).json()
    assert len(discover["featured"]) == min(10, len(CATEGORY_SEEDS))
    assert [c["slug"] for c in discover["categories"]] == [s.slug for s in CATEGORY_SEEDS]
    assert all(c["wallpaper_count"] == 2 for c in discover["categories"])
    first = discover["latest"][0]
    assert first["source_type"] == "ai_inhouse"
    assert (await client.get(first["url"])).status_code == 200
    assert (await client.get(first["thumb_url"])).status_code == 200

    page = (await client.get("/v1/wallpapers?category=space&limit=1")).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["category"] == "space"
    nxt = (
        await client.get(f"/v1/wallpapers?category=space&limit=1&cursor={page['next_cursor']}")
    ).json()
    assert nxt["items"][0]["id"] != page["items"][0]["id"]

    detail = await client.get(f"/v1/wallpapers/{first['id']}")
    assert detail.status_code == 200

    rerun = await generate_catalog(
        container, per_category=2, max_usd=None, confirm_spend=False, dry_run=False
    )
    assert (rerun.generated, rerun.skipped_existing) == (0, 2 * len(CATEGORY_SEEDS))


async def test_unpublished_wallpapers_are_hidden(
    client: httpx2.AsyncClient, container: Container
) -> None:
    await generate_catalog(
        container, per_category=1, max_usd=None, confirm_spend=False, dry_run=False
    )
    wid = (await client.get("/v1/discover")).json()["latest"][0]["id"]
    await (
        container.db.collection(WALLPAPERS)
        .document(wid)
        .update({"status": WallpaperStatus.REMOVED.value})
    )
    assert (await client.get(f"/v1/wallpapers/{wid}")).status_code == 404
    assert wid not in [w["id"] for w in (await client.get("/v1/discover")).json()["latest"]]


async def test_blocked_and_failed_outputs_are_not_published(
    client: httpx2.AsyncClient, container: Container
) -> None:
    provider = container.image_provider
    assert isinstance(provider, FakeImageProvider)
    provider.enqueue(FakeOutcome(error=ProviderError("provider_rejected", "x", retryable=False)))
    moderation = container.moderation
    assert isinstance(moderation, FakeModerationProvider)
    report = await generate_catalog(
        container, per_category=1, max_usd=None, confirm_spend=False, dry_run=True
    )
    assert report.generated == 0 and len(report.planned) == len(CATEGORY_SEEDS)  # dry run: no calls
    assert provider.calls == []

    report = await generate_catalog(
        container, per_category=1, max_usd=None, confirm_spend=False, dry_run=False
    )
    assert report.failed == 1
    moderation.block_images = True
    assert (
        await generate_catalog(
            container, per_category=1, max_usd=None, confirm_spend=False, dry_run=False
        )
    ).blocked == 1


@pytest.mark.parametrize(
    "container", [{"image_provider": "openai", "openai_api_key": "test-not-real"}], indirect=True
)
async def test_paid_provider_requires_explicit_confirmation(container: Container) -> None:
    # The container fixture keeps the fake provider object, but settings say "openai":
    # the guard must trigger on the configured provider before any call.
    with pytest.raises(SystemExit, match="confirm-spend"):
        await generate_catalog(
            container, per_category=1, max_usd=1.0, confirm_spend=False, dry_run=False
        )
    with pytest.raises(SystemExit, match="confirm-spend"):
        await generate_catalog(
            container, per_category=1, max_usd=None, confirm_spend=True, dry_run=False
        )


@pytest.mark.parametrize(
    "container", [{"image_provider": "openai", "openai_api_key": "test-not-real"}], indirect=True
)
async def test_budget_stops_the_run(container: Container) -> None:
    provider = container.image_provider
    assert isinstance(provider, FakeImageProvider)
    report = await generate_catalog(
        container, per_category=4, max_usd=0.25, confirm_spend=True, dry_run=False
    )
    assert report.stopped_for_budget
    assert report.spent_micros <= 250_000
    assert 0 < report.generated < 4 * len(CATEGORY_SEEDS)
    assert len(provider.calls) == report.generated
