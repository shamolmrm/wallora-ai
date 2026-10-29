"""End-to-end generation tests: HTTP API + worker + PostgreSQL, with fake providers."""

import asyncio
import io
import random
import uuid
from typing import Any

import httpx2
import pytest
from PIL import Image

from app.container import Container
from app.db.documents import JobDoc
from app.db.firestore import CREDIT_LEDGER, GENERATION_JOBS, MODERATION_CHECKS, SPEND_COUNTERS
from app.providers.image.base import ContentBlockedError, ProviderError
from app.providers.image.fake import FakeImageProvider, FakeOutcome
from app.providers.moderation.fake import FakeModerationProvider
from app.worker.processor import GenerationWorker
from tests.conftest import MutableClock, auth

pytestmark = pytest.mark.firestore

PROMPT = "misty pine forest at dawn, volumetric light"


def body(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"prompt": PROMPT, "tier": "standard", "aspect": "phone"}
    values.update(overrides)
    return values


async def create(
    client: httpx2.AsyncClient, key: str | None = None, uid: str = "alice", **overrides: Any
) -> httpx2.Response:
    headers = {**auth(uid), "Idempotency-Key": key or uuid.uuid4().hex}
    return await client.post("/v1/generations", json=body(**overrides), headers=headers)


async def balance(client: httpx2.AsyncClient, uid: str = "alice") -> tuple[int, int]:
    data = (await client.get("/v1/me/credits", headers=auth(uid))).json()
    return data["available"], data["held"]


def worker(container: Container) -> GenerationWorker:
    return GenerationWorker(container, worker_id="w-test", rng=random.Random(0))  # noqa: S311 - deterministic jitter


async def run_one(container: Container) -> str | None:
    w = worker(container)
    claim = await w.claim()
    if claim is None:
        return None
    await w.process(claim)
    return claim.job_id


async def job_row(container: Container, job_id: str | uuid.UUID) -> JobDoc:
    snap = await container.db.collection(GENERATION_JOBS).document(str(job_id)).get()
    assert snap.exists
    return JobDoc.from_doc(snap.to_dict() or {})


async def docs(container: Container, collection: str) -> list[dict[str, Any]]:
    return [snap.to_dict() or {} async for snap in container.db.collection(collection).stream()]


async def counters(container: Container) -> dict[tuple[str, str], dict[str, Any]]:
    return {(d["scope"], d["period"]): d for d in await docs(container, SPEND_COUNTERS)}


def fake_provider(container: Container) -> FakeImageProvider:
    provider = container.image_provider
    assert isinstance(provider, FakeImageProvider)
    return provider


def fake_moderation(container: Container) -> FakeModerationProvider:
    moderation = container.moderation
    assert isinstance(moderation, FakeModerationProvider)
    return moderation


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 600), (12, 80, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- auth & validation


async def test_authentication_is_required(client: httpx2.AsyncClient) -> None:
    no_header = await client.post(
        "/v1/generations", json=body(), headers={"Idempotency-Key": "k" * 16}
    )
    assert no_header.status_code == 401
    assert no_header.headers["content-type"] == "application/problem+json"
    assert no_header.json()["code"] == "unauthenticated"

    bad = await client.post(
        "/v1/generations",
        json=body(),
        headers={"Authorization": "Bearer nope", "Idempotency-Key": "k" * 16},
    )
    assert bad.status_code == 401

    anon = await client.post(
        "/v1/generations",
        json=body(),
        headers={"Authorization": "Bearer anon:guest1", "Idempotency-Key": "k" * 16},
    )
    assert anon.status_code == 403
    assert anon.json()["code"] == "account_required"


async def test_idempotency_key_is_required_and_validated(client: httpx2.AsyncClient) -> None:
    missing = await client.post("/v1/generations", json=body(), headers=auth())
    assert missing.status_code == 400
    assert missing.json()["code"] == "idempotency_key_required"
    invalid = await create(client, key="bad key!")
    assert invalid.json()["code"] == "idempotency_key_invalid"


@pytest.mark.parametrize(
    ("overrides", "status", "code"),
    [
        ({"tier": "hd"}, 403, "tier_requires_premium"),
        ({"tier": "nope"}, 422, "invalid_tier"),
        ({"tier": "draft", "aspect": "phone"}, 422, "aspect_not_supported"),
        ({"aspect": "phone_hd"}, 422, "aspect_not_supported"),
        ({"prompt": "  "}, 422, "prompt_required"),
        ({"style_preset": "unknown"}, 422, "invalid_style_preset"),
        ({"kind": "edit"}, 422, "invalid_inputs"),
        ({"kind": "variation"}, 422, "invalid_inputs"),
    ],
)
async def test_request_validation(
    client: httpx2.AsyncClient, overrides: dict[str, Any], status: int, code: str
) -> None:
    response = await create(client, **overrides)
    assert response.status_code == status
    assert response.json()["code"] == code


async def test_capabilities_reflect_model_limits(client: httpx2.AsyncClient) -> None:
    data = (await client.get("/v1/studio/capabilities")).json()
    tiers = {tier["name"]: tier for tier in data["tiers"]}
    assert data["provider"] == "fake"
    assert "phone" not in tiers["draft"]["aspects"]  # gpt-image-1-mini: fixed sizes only
    assert "phone" in tiers["standard"]["aspects"]
    assert all(a["name"] != "phone_hd" for a in data["aspects"])  # experimental disabled


# --------------------------------------------------------------------------- idempotency & credits


async def test_create_reserves_credits_and_replays(client: httpx2.AsyncClient) -> None:
    first = await create(client, key="key-00000001")
    assert first.status_code == 202
    job = first.json()
    assert job["status"] == "queued"
    assert job["credit_cost"] == 3
    assert await balance(client) == (7, 3)

    replay = await create(client, key="key-00000001")
    assert replay.status_code == 200
    assert replay.json()["id"] == job["id"]
    assert await balance(client) == (7, 3)

    conflict = await create(client, key="key-00000001", prompt="something else entirely")
    assert conflict.status_code == 422
    assert conflict.json()["code"] == "idempotency_key_reused"


async def test_concurrent_requests_with_same_key_create_one_job(
    client: httpx2.AsyncClient,
) -> None:
    # 5 concurrent requests: the Firestore emulator can hold locks for up to 30 s under
    # contention (documented emulator limitation), so higher fan-out only slows CI down.
    responses = await asyncio.gather(*(create(client, key="same-key-123") for _ in range(5)))
    assert sorted(r.status_code for r in responses) == [200] * 4 + [202]
    assert len({r.json()["id"] for r in responses}) == 1
    assert await balance(client) == (7, 3)


async def test_concurrent_requests_cannot_overspend(client: httpx2.AsyncClient) -> None:
    # 5 concurrent requests: the Firestore emulator can hold locks for up to 30 s under
    # contention (documented emulator limitation), so higher fan-out only slows CI down.
    responses = await asyncio.gather(*(create(client) for _ in range(5)))
    statuses = [r.status_code for r in responses]
    assert statuses.count(202) == 3
    assert statuses.count(402) == 2
    assert await balance(client) == (1, 9)


@pytest.mark.parametrize("container", [{"welcome_credits": 2}], indirect=True)
async def test_insufficient_credits(client: httpx2.AsyncClient) -> None:
    response = await create(client)
    assert response.status_code == 402
    problem = response.json()
    assert (problem["code"], problem["required"], problem["available"]) == (
        "insufficient_credits",
        3,
        2,
    )
    assert (await client.get("/v1/generations", headers=auth())).json()["items"] == []


async def test_ledger_records_every_movement(
    client: httpx2.AsyncClient, container: Container
) -> None:
    job_id = (await create(client)).json()["id"]
    await run_one(container)
    page = (await client.get("/v1/me/credits/ledger", headers=auth())).json()
    types = [entry["entry_type"] for entry in reversed(page["items"])]
    assert types == ["grant", "reserve", "capture"]
    ledger = [
        snap.to_dict() or {} async for snap in container.db.collection_group(CREDIT_LEDGER).stream()
    ]
    assert job_id in [entry["job_id"] for entry in ledger]


# --------------------------------------------------------------------------- moderation & limits


async def test_blocked_prompts_are_rejected_without_charge(
    client: httpx2.AsyncClient, container: Container
) -> None:
    by_model = await create(client, prompt="a castle [[block]] at night")
    by_blocklist = await create(client, prompt="explicit porn scene")
    for response in (by_model, by_blocklist):
        assert response.status_code == 422
        assert response.json()["code"] == "prompt_rejected"
    assert await balance(client) == (10, 0)
    checks = await docs(container, MODERATION_CHECKS)
    assert sorted(c["provider"] for c in checks) == ["blocklist", "fake"]
    assert all(c["decision"] == "block" for c in checks)


async def test_moderation_outage_fails_closed(
    client: httpx2.AsyncClient, container: Container
) -> None:
    fake_moderation(container).fail_next = 1
    response = await create(client)
    assert response.status_code == 503
    assert response.json()["code"] == "moderation_unavailable"
    assert (await client.get("/v1/generations", headers=auth())).json()["items"] == []


@pytest.mark.parametrize("container", [{"abuse_strike_threshold": 2}], indirect=True)
async def test_repeated_blocks_restrict_the_account(client: httpx2.AsyncClient) -> None:
    for _ in range(2):
        assert (await create(client, prompt="[[block]] again")).status_code == 422
    response = await create(client)
    assert response.status_code == 403
    assert response.json()["code"] == "generation_restricted"


@pytest.mark.parametrize("container", [{"generation_enabled": False}], indirect=True)
async def test_kill_switch(client: httpx2.AsyncClient) -> None:
    response = await create(client)
    assert response.status_code == 503
    assert response.json()["code"] == "generation_paused"


@pytest.mark.parametrize("container", [{"global_daily_cost_cap_usd": 0.01}], indirect=True)
async def test_global_cost_cap_pauses_generation_without_charge(
    client: httpx2.AsyncClient, container: Container
) -> None:
    response = await create(client)
    assert response.status_code == 503
    assert response.json()["code"] == "generation_paused"
    assert await balance(client) == (10, 0)
    assert await docs(container, SPEND_COUNTERS) == []


@pytest.mark.parametrize("container", [{"user_daily_generation_cap": 1}], indirect=True)
async def test_user_daily_cap(client: httpx2.AsyncClient) -> None:
    assert (await create(client)).status_code == 202
    response = await create(client)
    assert response.status_code == 429
    assert response.json()["code"] == "daily_limit_reached"
    assert (await create(client, uid="bob")).status_code == 202  # other users unaffected


@pytest.mark.parametrize("container", [{"user_generations_per_minute": 2}], indirect=True)
async def test_rate_limit_per_minute(client: httpx2.AsyncClient, clock: MutableClock) -> None:
    assert (await create(client)).status_code == 202
    assert (await create(client)).status_code == 202
    limited = await create(client)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    clock.advance(seconds=61)
    # The rate window has passed: 4 of 10 credits remain, so one more 3-credit job fits.
    assert (await create(client)).status_code == 202


# --------------------------------------------------------------------------- worker outcomes


async def test_successful_generation(client: httpx2.AsyncClient, container: Container) -> None:
    job_id = (await create(client, style_preset="minimal")).json()["id"]

    assert str(await run_one(container)) == job_id

    data = (await client.get(f"/v1/generations/{job_id}", headers=auth())).json()
    assert data["status"] == "succeeded"
    assert data["attempts"] == 1
    assert len(data["images"]) == 1
    image = data["images"][0]
    assert (image["width"], image["height"]) == (1072, 2320)
    fetched = await client.get(image["url"])
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/webp"
    assert (await client.get(image["thumb_url"])).status_code == 200
    assert await balance(client) == (7, 0)

    call = fake_provider(container).calls[-1]
    assert call[0] == "generate"
    assert call[1].prompt.startswith(PROMPT)
    assert "Minimalist" in call[1].prompt
    assert call[1].size == "1072x2320"
    assert call[1].safety_identifier and "alice" not in call[1].safety_identifier

    job = await job_row(container, job_id)
    assert job.actual_cost_usd_micros > 0
    assert not job.billing_uncertain
    found = await counters(container)
    # Spend counters hold the actual cost once the estimate has been corrected.
    assert found[("global", "day")]["cost_usd_micros"] == job.actual_cost_usd_micros
    assert found[("user", "day")]["job_count"] == 1


async def test_tampered_signed_url_is_rejected(
    client: httpx2.AsyncClient, container: Container
) -> None:
    job_id = (await create(client)).json()["id"]
    await run_one(container)
    url = (await client.get(f"/v1/generations/{job_id}", headers=auth())).json()["images"][0]["url"]
    assert (await client.get(url.replace("sig=", "sig=0"))).status_code == 404
    other_key = url.replace("/gen/", "/ref/")
    assert (await client.get(other_key)).status_code == 404


async def test_retryable_error_is_retried_with_backoff(
    client: httpx2.AsyncClient, container: Container, clock: MutableClock
) -> None:
    fake_provider(container).enqueue(
        FakeOutcome(error=ProviderError("rate_limited", "slow down", retryable=True))
    )
    job_id = (await create(client)).json()["id"]

    await run_one(container)
    job = await job_row(container, job_id)
    assert (job.status, job.attempts, job.error_code) == ("queued", 1, "rate_limited")
    assert job.next_attempt_at > clock()
    assert await run_one(container) is None  # backoff not yet elapsed

    clock.advance(seconds=10)
    await run_one(container)
    job = await job_row(container, job_id)
    assert (job.status, job.attempts) == ("succeeded", 2)
    assert await balance(client) == (7, 0)


async def test_exhausted_retries_fail_and_refund(
    client: httpx2.AsyncClient, container: Container, clock: MutableClock
) -> None:
    error = ProviderError("provider_unavailable", "down", retryable=True)
    fake_provider(container).enqueue(*(FakeOutcome(error=error) for _ in range(3)))
    job_id = (await create(client)).json()["id"]

    for _ in range(3):
        await run_one(container)
        clock.advance(seconds=10)

    job = await job_row(container, job_id)
    assert (job.status, job.attempts, job.error_code) == ("failed", 3, "provider_unavailable")
    assert await balance(client) == (10, 0)
    assert await run_one(container) is None
    user_day = (await counters(container))[("user", "day")]
    assert (user_day["cost_usd_micros"], user_day["job_count"]) == (0, 0)


async def test_non_retryable_error_fails_immediately_and_refunds(
    client: httpx2.AsyncClient, container: Container
) -> None:
    fake_provider(container).enqueue(
        FakeOutcome(error=ProviderError("provider_rejected", "bad", retryable=False))
    )
    job_id = (await create(client)).json()["id"]
    await run_one(container)
    job = await job_row(container, job_id)
    assert (job.status, job.attempts) == ("failed", 1)
    assert await balance(client) == (10, 0)


@pytest.mark.parametrize("container", [{"job_max_attempts": 1}], indirect=True)
async def test_timeout_counts_estimated_cost_and_refunds(
    client: httpx2.AsyncClient, container: Container
) -> None:
    fake_provider(container).enqueue(
        FakeOutcome(error=ProviderError("timeout", "slow", retryable=True, billing_uncertain=True))
    )
    job_id = (await create(client)).json()["id"]
    await run_one(container)
    job = await job_row(container, job_id)
    assert job.status == "failed"
    assert job.billing_uncertain
    assert job.actual_cost_usd_micros == job.estimated_cost_usd_micros
    assert await balance(client) == (10, 0)
    global_day = (await counters(container))[("global", "day")]
    assert global_day["cost_usd_micros"] == job.estimated_cost_usd_micros


async def test_provider_safety_block_fails_refunds_and_records_strike(
    client: httpx2.AsyncClient, container: Container
) -> None:
    fake_provider(container).enqueue(FakeOutcome(error=ContentBlockedError()))
    job_id = (await create(client)).json()["id"]
    await run_one(container)
    job = await job_row(container, job_id)
    assert (job.status, job.error_code) == ("failed", "content_blocked")
    assert await balance(client) == (10, 0)
    (check,) = await docs(container, MODERATION_CHECKS)
    assert (check["subject_type"], check["decision"], check["provider"]) == (
        "output",
        "block",
        "fake-safety",
    )


async def test_output_moderation_block_discards_image(
    client: httpx2.AsyncClient, container: Container
) -> None:
    job_id = (await create(client)).json()["id"]
    fake_moderation(container).block_images = True
    await run_one(container)
    job = await job_row(container, job_id)
    assert (job.status, job.error_code) == ("failed", "content_blocked")
    assert await balance(client) == (10, 0)
    storage = container.storage
    assert not [key for key in getattr(storage, "objects", {}) if "/gen/" in key]
    checks = await docs(container, MODERATION_CHECKS)
    assert len(checks) == 1  # recorded once, not twice


# --------------------------------------------------------------------------- cancel, reaper, leases


async def test_cancel_only_while_queued(client: httpx2.AsyncClient, container: Container) -> None:
    queued = (await create(client)).json()["id"]
    cancelled = await client.post(f"/v1/generations/{queued}/cancel", headers=auth())
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert await balance(client) == (10, 0)
    assert await run_one(container) is None

    processing = (await create(client)).json()["id"]
    assert await worker(container).claim() is not None
    conflict = await client.post(f"/v1/generations/{processing}/cancel", headers=auth())
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "not_cancellable"


async def test_reaper_requeues_then_fails_and_refunds(
    client: httpx2.AsyncClient, container: Container, clock: MutableClock
) -> None:
    job_id = (await create(client)).json()["id"]
    w = worker(container)
    lease = container.settings.job_lease_seconds
    for attempt in range(1, 4):
        assert await w.claim() is not None  # worker "crashes" without processing
        clock.advance(seconds=lease + 1)
        assert await w.reap_expired_leases() == 1
        job = await job_row(container, job_id)
        assert job.attempts == attempt
        clock.advance(seconds=10)
    job = await job_row(container, job_id)
    assert (job.status, job.error_code) == ("failed", "lease_expired")
    assert job.billing_uncertain
    assert await balance(client) == (10, 0)


async def test_late_result_after_lease_loss_is_discarded(
    client: httpx2.AsyncClient, container: Container, clock: MutableClock
) -> None:
    job_id = (await create(client)).json()["id"]
    w = worker(container)
    claim = await w.claim()
    assert claim is not None
    clock.advance(seconds=container.settings.job_lease_seconds + 1)
    await w.reap_expired_leases()

    await w.process(claim)  # the slow worker finally finishes

    job = await job_row(container, job_id)
    assert job.status == "queued"  # still waiting for a fresh attempt
    assert not [key for key in getattr(container.storage, "objects", {}) if "/gen/" in key]
    assert await balance(client) == (7, 3)


# --------------------------------------------------------------------------- edits & variations


async def upload(
    client: httpx2.AsyncClient, uid: str = "alice", data: bytes | None = None
) -> httpx2.Response:
    files = {"file": ("ref.png", data if data is not None else png_bytes(), "image/png")}
    return await client.post("/v1/uploads", files=files, headers=auth(uid))


async def test_edit_with_reference_upload(client: httpx2.AsyncClient, container: Container) -> None:
    uploaded = await upload(client)
    assert uploaded.status_code == 201
    upload_id = uploaded.json()["id"]

    job_id = (
        await create(client, kind="edit", prompt="make it snowy", reference_upload_ids=[upload_id])
    ).json()["id"]
    await run_one(container)

    assert (await job_row(container, job_id)).status == "succeeded"
    operation, request = fake_provider(container).calls[-1]
    assert operation == "edit"
    assert request.model == "gpt-image-2.5-sunburst"
    assert len(request.images) == 1
    assert request.images[0].data.startswith(bytes([0x89]) + b"PNG")


async def test_cannot_use_another_users_upload(client: httpx2.AsyncClient) -> None:
    bob_upload = (await upload(client, uid="bob")).json()["id"]
    response = await create(client, kind="edit", prompt="snow", reference_upload_ids=[bob_upload])
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_reference"


async def test_variation_of_own_image(client: httpx2.AsyncClient, container: Container) -> None:
    first = (await create(client)).json()["id"]
    await run_one(container)
    image_id = (await client.get(f"/v1/generations/{first}", headers=auth())).json()["images"][0][
        "id"
    ]

    variation = await create(client, kind="variation", prompt=None, source_image_id=image_id)
    assert variation.status_code == 202
    await run_one(container)

    assert (await job_row(container, variation.json()["id"])).status == "succeeded"
    operation, request = fake_provider(container).calls[-1]
    assert operation == "edit"
    assert request.prompt.startswith("Create a new variation")
    other = await create(client, uid="bob", kind="variation", source_image_id=image_id)
    assert other.json()["code"] == "invalid_source_image"


async def test_upload_validation_and_moderation(
    client: httpx2.AsyncClient, container: Container
) -> None:
    gif = await upload(client, data=b"GIF89a" + bytes(64))
    assert gif.status_code == 422
    assert gif.json()["code"] == "unsupported_format"

    fake_moderation(container).block_images = True
    blocked = await upload(client)
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "upload_rejected"


@pytest.mark.parametrize("container", [{"upload_max_bytes": 2048}], indirect=True)
async def test_upload_size_limit(client: httpx2.AsyncClient) -> None:
    response = await upload(client, data=bytes(4096))
    assert response.status_code == 413


# ------------------------------------------------------------------- ownership, retry, delete


async def test_other_users_cannot_see_or_cancel_jobs(client: httpx2.AsyncClient) -> None:
    job_id = (await create(client)).json()["id"]
    assert (await client.get(f"/v1/generations/{job_id}", headers=auth("bob"))).status_code == 404
    cancel = await client.post(f"/v1/generations/{job_id}/cancel", headers=auth("bob"))
    assert cancel.status_code == 404
    assert (await client.get("/v1/generations", headers=auth("bob"))).json()["items"] == []


async def test_retry_creates_linked_job(client: httpx2.AsyncClient, container: Container) -> None:
    fake_provider(container).enqueue(
        FakeOutcome(error=ProviderError("provider_rejected", "bad", retryable=False))
    )
    failed = (await create(client)).json()["id"]
    await run_one(container)

    headers = {**auth(), "Idempotency-Key": "retry-key-1"}
    retried = await client.post(f"/v1/generations/{failed}/retry", headers=headers)
    assert retried.status_code == 202
    assert retried.json()["retry_of_job_id"] == failed
    again = await client.post(f"/v1/generations/{failed}/retry", headers=headers)
    assert (again.status_code, again.json()["id"]) == (200, retried.json()["id"])

    await run_one(container)
    succeeded = retried.json()["id"]
    headers = {**auth(), "Idempotency-Key": "retry-key-2"}
    not_retryable = await client.post(f"/v1/generations/{succeeded}/retry", headers=headers)
    assert not_retryable.status_code == 409


async def test_delete_erases_prompt_and_objects(
    client: httpx2.AsyncClient, container: Container
) -> None:
    job_id = (await create(client)).json()["id"]
    assert (await client.delete(f"/v1/generations/{job_id}", headers=auth())).status_code == 409
    await run_one(container)

    assert (await client.delete(f"/v1/generations/{job_id}", headers=auth())).status_code == 204
    assert (await client.get(f"/v1/generations/{job_id}", headers=auth())).status_code == 404
    job = await job_row(container, job_id)
    assert job.prompt is None
    assert not [key for key in getattr(container.storage, "objects", {}) if "/gen/" in key]


async def test_history_pagination(client: httpx2.AsyncClient) -> None:
    ids = [(await create(client, tier="draft", aspect="portrait")).json()["id"] for _ in range(3)]
    page1 = (await client.get("/v1/generations?limit=2", headers=auth())).json()
    assert [item["id"] for item in page1["items"]] == ids[::-1][:2]
    cursor = page1["next_cursor"]
    page2 = (await client.get(f"/v1/generations?limit=2&cursor={cursor}", headers=auth())).json()
    assert [item["id"] for item in page2["items"]] == [ids[0]]
    assert page2["next_cursor"] is None
