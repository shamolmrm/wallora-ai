"""Application settings loaded from environment variables.

Every value has a safe local default. ``validate_for_environment`` refuses
unsafe combinations (fake providers, in-memory storage, debug) outside local
and test environments, so a misconfigured deployment fails at startup instead
of silently serving fake images or leaking data.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"


class ImageProviderName(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"


class ModerationProviderName(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"


class StorageBackend(StrEnum):
    MEMORY = "memory"  # tests only
    LOCAL = "local"  # filesystem + HMAC-signed URLs served by the API (development)
    S3 = "s3"  # Cloudflare R2 or any S3-compatible store


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: AppEnv = AppEnv.LOCAL
    app_debug: bool = False
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://wallora@localhost:5432/wallora"
    database_pool_size: int = Field(default=10, ge=1, le=100)

    # --- Auth ---
    firebase_project_id: str | None = None

    # --- Storage ---
    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_local_dir: str = "./var/storage"
    storage_signing_secret: SecretStr = SecretStr("local-dev-only-signing-secret-change-me")
    storage_endpoint_url: str | None = None
    storage_region: str = "auto"
    storage_access_key_id: SecretStr | None = None
    storage_secret_access_key: SecretStr | None = None
    storage_bucket_private: str = "wallora-user-private"
    signed_url_ttl_seconds: int = Field(default=600, ge=30, le=3600)

    # --- AI providers ---
    image_provider: ImageProviderName = ImageProviderName.FAKE
    moderation_provider: ModerationProviderName = ModerationProviderName.FAKE
    openai_api_key: SecretStr | None = None
    openai_org_id: str | None = None
    openai_project_id: str | None = None
    openai_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    openai_moderation_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    openai_moderation_model: str = "omni-moderation-latest"

    # --- Studio / generation ---
    generation_enabled: bool = True  # global kill switch
    allow_experimental_sizes: bool = False
    welcome_credits: int = Field(default=3, ge=0)
    daily_free_credits: int = Field(default=1, ge=0)
    prompt_max_chars: int = Field(default=1000, ge=10, le=32000)
    max_reference_images: int = Field(default=4, ge=1, le=16)

    # --- Worker ---
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    job_lease_seconds: int = Field(default=300, ge=30)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_seconds: float = Field(default=5.0, gt=0)
    retry_max_delay_seconds: float = Field(default=60.0, gt=0)
    storage_max_attempts: int = Field(default=3, ge=1, le=10)

    # --- Spend limits and quotas (bootstrap defaults; admin-editable in a later phase) ---
    global_daily_cost_cap_usd: float = Field(default=5.0, ge=0)
    global_monthly_cost_cap_usd: float = Field(default=50.0, ge=0)
    user_daily_cost_cap_usd: float = Field(default=0.25, ge=0)
    user_daily_generation_cap: int = Field(default=10, ge=0)
    user_generations_per_minute: int = Field(default=6, ge=1)
    user_generations_per_hour: int = Field(default=60, ge=1)
    abuse_strike_threshold: int = Field(default=5, ge=1)
    abuse_strike_window_hours: int = Field(default=24, ge=1)

    # --- Uploads ---
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    upload_max_pixels: int = Field(default=40_000_000, ge=1)
    upload_max_side_px: int = Field(default=2048, ge=256)
    uploads_per_hour: int = Field(default=30, ge=1)
    reference_retention_days: int = Field(default=7, ge=1)

    @model_validator(mode="after")
    def validate_for_environment(self) -> Self:
        deployed = self.app_env in (AppEnv.STAGING, AppEnv.PROD)
        problems: list[str] = []
        if deployed:
            if self.app_debug:
                problems.append("APP_DEBUG must be false")
            if self.storage_backend is not StorageBackend.S3:
                problems.append("STORAGE_BACKEND must be s3")
            if self.storage_signing_secret.get_secret_value().startswith("local-dev-only"):
                problems.append("STORAGE_SIGNING_SECRET must be set")
            if not self.firebase_project_id:
                problems.append("FIREBASE_PROJECT_ID is required")
        if self.app_env is AppEnv.PROD:
            if self.image_provider is ImageProviderName.FAKE:
                problems.append("IMAGE_PROVIDER=fake is not allowed in prod")
            if self.moderation_provider is ModerationProviderName.FAKE:
                problems.append("MODERATION_PROVIDER=fake is not allowed in prod")
        uses_openai = (
            self.image_provider is ImageProviderName.OPENAI
            or self.moderation_provider is ModerationProviderName.OPENAI
        )
        if uses_openai and not self.openai_api_key:
            problems.append("OPENAI_API_KEY is required when an OpenAI provider is selected")
        if self.storage_backend is StorageBackend.S3 and not (
            self.storage_endpoint_url
            and self.storage_access_key_id
            and self.storage_secret_access_key
        ):
            problems.append("S3 storage requires endpoint URL and access keys")
        if problems:
            raise ValueError("Unsafe configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
