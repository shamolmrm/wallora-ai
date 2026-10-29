import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

PROD_ENV = {
    "APP_ENV": "prod",
    "FIREBASE_PROJECT_ID": "test-project",
    "STORAGE_BACKEND": "s3",
    "STORAGE_ENDPOINT_URL": "https://example.invalid",
    "STORAGE_ACCESS_KEY_ID": "placeholder",
    "STORAGE_SECRET_ACCESS_KEY": "placeholder",
    "STORAGE_SIGNING_SECRET": "placeholder-signing-secret",
    "IMAGE_PROVIDER": "openai",
    "MODERATION_PROVIDER": "openai",
    "OPENAI_API_KEY": "placeholder-not-a-real-key",
}


def test_live_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_disabled_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in PROD_ENV.items():
        monkeypatch.setenv(name, value)
    # emulators:exec exports this; prod settings rightly refuse to start with it.
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    get_settings.cache_clear()
    try:
        # No `with`: the lifespan (which would build real clients) is not started.
        client = TestClient(create_app())
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    finally:
        get_settings.cache_clear()
