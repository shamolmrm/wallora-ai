"""Wiring of settings, database, storage and providers.

The API and the worker build the same container; tests build one with fakes.
Nothing here performs network calls at construction time.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import ImageProviderName, ModerationProviderName, Settings, StorageBackend
from app.db.base import utcnow
from app.db.session import SessionFactory, create_engine, create_session_factory
from app.integrations.auth import FirebaseTokenVerifier, TokenVerifier
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


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessions: SessionFactory
    storage: ObjectStorage
    image_provider: ImageProvider
    moderation: ModerationProvider
    token_verifier: TokenVerifier
    url_signer: UrlSigner | None = None
    clock: Callable[[], datetime] = field(default=utcnow)

    async def close(self) -> None:
        await self.engine.dispose()


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
    engine = create_engine(settings)
    storage, signer = build_storage(settings)
    return Container(
        settings=settings,
        engine=engine,
        sessions=create_session_factory(engine),
        storage=storage,
        image_provider=build_image_provider(settings),
        moderation=build_moderation(settings),
        token_verifier=FirebaseTokenVerifier(settings.firebase_project_id),
        url_signer=signer,
    )
