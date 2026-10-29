import os
import random
import uuid

import pytest
from pydantic import ValidationError

from app.core import ids
from app.core.logging import redact
from app.worker.processor import backoff_seconds
from tests.conftest import make_settings


@pytest.mark.parametrize("generator", [ids.new_id, ids._fallback_uuid7])
def test_uuid7_layout(generator: object) -> None:
    assert callable(generator)
    values = [generator() for _ in range(200)]
    for value in values:
        assert isinstance(value, uuid.UUID)
        assert value.version == 7
        assert value.variant == uuid.RFC_4122
    assert len(set(values)) == len(values)


def test_fallback_uuid7_is_time_ordered_across_milliseconds() -> None:
    first = ids._fallback_uuid7()
    import time  # noqa: PLC0415

    time.sleep(0.002)
    assert ids._fallback_uuid7() > first


def test_test_settings_use_fakes() -> None:
    settings = make_settings()
    assert settings.image_provider == "fake"
    assert settings.moderation_provider == "fake"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"app_env": "prod"}, "IMAGE_PROVIDER=fake is not allowed in prod"),
        ({"app_env": "staging"}, "STORAGE_BACKEND must be s3"),
        ({"image_provider": "openai"}, "OPENAI_API_KEY is required"),
        ({"storage_backend": "s3"}, "S3 storage requires endpoint URL and access keys"),
    ],
)
def test_unsafe_configuration_is_rejected(overrides: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings(**overrides)


def test_redaction() -> None:
    text = "Authorization: Bearer abc.def.ghi123 key sk-proj-ABCDEFGHIJKLMNOPQRST password=hunter2"
    redacted = redact(text)
    assert "abc.def.ghi123" not in redacted
    assert "ABCDEFGHIJKLMNOPQRST" not in redacted
    assert "hunter2" not in redacted


def test_backoff_is_exponential_capped_and_jittered() -> None:
    rng = random.Random(1)  # noqa: S311 - deterministic jitter
    delays = [backoff_seconds(n, 5.0, 60.0, rng) for n in range(1, 8)]
    caps = [5, 10, 20, 40, 60, 60, 60]
    for delay, cap in zip(delays, caps, strict=True):
        assert cap * 0.5 <= delay <= cap


def test_dev_auth_is_refused_outside_local() -> None:
    with pytest.raises(ValidationError, match="DEV_AUTH_ENABLED"):
        make_settings(app_env="staging", dev_auth_enabled=True)


async def test_dev_token_verifier() -> None:
    from app.integrations.auth import AuthError, DevTokenVerifier  # noqa: PLC0415

    identity = await DevTokenVerifier().verify("dev:phone1")
    assert identity.uid == "dev-phone1"
    for bad in ("phone1", "dev:", "dev:a b", "firebase:abc"):
        with pytest.raises(AuthError):
            await DevTokenVerifier().verify(bad)


def test_emulator_host_from_settings_reaches_the_client_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.firestore import create_client  # noqa: PLC0415

    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    client = create_client(
        make_settings(firestore_emulator_host="127.0.0.1:8085", firestore_project_id="demo-x")
    )
    try:
        assert os.environ["FIRESTORE_EMULATOR_HOST"] == "127.0.0.1:8085"
    finally:
        client.close()  # type: ignore[no-untyped-call]
