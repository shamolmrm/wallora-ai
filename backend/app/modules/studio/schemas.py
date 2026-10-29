import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.documents import JobKind


class CreateGenerationRequest(BaseModel):
    kind: JobKind = JobKind.GENERATE
    # Upper bound before normalisation; the configured limit is enforced afterwards.
    prompt: str | None = Field(default=None, max_length=32000)
    tier: str = "standard"
    aspect: str = "phone"
    style_preset: str | None = None
    reference_upload_ids: list[uuid.UUID] = Field(default_factory=list, max_length=16)
    source_image_id: uuid.UUID | None = None


class ImageOut(BaseModel):
    id: uuid.UUID
    width: int
    height: int
    mime_type: str
    url: str
    thumb_url: str
    url_expires_in: int


class GenerationOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    prompt: str | None
    style_preset: str | None
    tier: str
    aspect: str
    width: int
    height: int
    credit_cost: int
    attempts: int
    error_code: str | None
    retry_of_job_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    images: list[ImageOut] = Field(default_factory=list)


class GenerationPage(BaseModel):
    items: list[GenerationOut]
    next_cursor: str | None


class UploadOut(BaseModel):
    id: uuid.UUID
    width: int
    height: int
    mime_type: str
    expires_at: datetime


class DownloadOut(BaseModel):
    url: str
    expires_in: int


class TierOut(BaseModel):
    name: str
    credits: int
    quality: str
    premium_only: bool
    aspects: list[str]
    supports_edit: bool
    max_reference_images: int


class AspectOut(BaseModel):
    name: str
    width: int
    height: int
    experimental: bool


class CapabilitiesOut(BaseModel):
    provider: str
    generation_enabled: bool
    kinds: list[str]
    tiers: list[TierOut]
    aspects: list[AspectOut]
    style_presets: list[str]
    prompt_max_chars: int
    upload_max_bytes: int
