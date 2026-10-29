"""Wiring of settings, database, storage and providers.

The API and the worker build the same container; tests build one with fakes.
Nothing here performs network calls at construction time.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from google.cloud.firestore_v1 import AsyncClient

from app.core.config import ImageProviderName, ModerationProviderName, Settings, StorageBackend
from app.db.firestore import create_client
from app.integrations.auth import DevTokenVerifier, FirebaseTokenVerifier, TokenVerifier
from app.integrations.storage import (
    LocalStorage,
    MemoryStorage,
    ObjectStorage,
    S3Storage,
    UrlSigner,
)
from app.providers.image.base import ImageProvider
from app.providers.image.fake import FakeImageProvider
from app.providers.image.openai import OpenAIImageProvider
from app.providers.moderation.base import ModerationProvider
from app.providers.moderation.fake import FakeModerationProvider
from app.providers.moderation.openai import OpenAIModerationProvider


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Container:
    settings: Settings
    db: AsyncClient
    storage: ObjectStorage
    image_provider: ImageProvider
    moderation: ModerationProvider
    token_verifier: TokenVerifier
    url_signer: UrlSigner | None = None
    clock: Callable[[], datetime] = field(default=utcnow)

    async def close(self) -> None:
        self.db.close()  # type: ignore[no-untyped-call]


def build_storage(settings: Settings) -> tuple[ObjectStorage, UrlSigner | None]:
    if settings.storage_backend is StorageBackend.S3:
        assert settings.storage_endpoint_url  # noqa: S101 - guaranteed by Settings validation
        assert settings.storage_access_key_id  # noqa: S101
        assert settings.storage_secret_access_key  # noqa: S101
        return (
            S3Storage(
                endpoint_url=settings.storage_endpoint_url,
                region=settings.storage_region,
                access_key_id=settings.storage_access_key_id.get_secret_value(),
                secret_access_key=settings.storage_secret_access_key.get_secret_value(),
                bucket=settings.storage_bucket_private,
            ),
            None,
        )
    signer = UrlSigner(
        settings.app_base_url, settings.storage_signing_secret.get_secret_value().encode()
    )
    if settings.storage_backend is StorageBackend.MEMORY:
        return MemoryStorage(signer), signer
    return LocalStorage(Path(settings.storage_local_dir), signer), signer


def build_image_provider(settings: Settings) -> ImageProvider:
    if settings.image_provider is ImageProviderName.OPENAI:
        assert settings.openai_api_key  # noqa: S101 - guaranteed by Settings validation
        return OpenAIImageProvider.create(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout_seconds=settings.openai_timeout_seconds,
            organization=settings.openai_org_id,
            project=settings.openai_project_id,
        )
    return FakeImageProvider()


def build_moderation(settings: Settings) -> ModerationProvider:
    if settings.moderation_provider is ModerationProviderName.OPENAI:
        assert settings.openai_api_key  # noqa: S101 - guaranteed by Settings validation
        return OpenAIModerationProvider.create(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_moderation_model,
            timeout_seconds=settings.openai_moderation_timeout_seconds,
            organization=settings.openai_org_id,
            project=settings.openai_project_id,
        )
    return FakeModerationProvider()


def build_container(settings: Settings) -> Container:
    storage, signer = build_storage(settings)
    return Container(
        settings=settings,
        db=create_client(settings),
        storage=storage,
        image_provider=build_image_provider(settings),
        moderation=build_moderation(settings),
        token_verifier=build_token_verifier(settings),
        url_signer=signer,
    )


def build_token_verifier(settings: Settings) -> TokenVerifier:
    # Settings validation guarantees dev auth is only possible in local/test.
    if settings.dev_auth_enabled:
        return DevTokenVerifier()
    return FirebaseTokenVerifier(settings.firebase_project_id)
