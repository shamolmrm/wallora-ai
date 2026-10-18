import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow


class JobKind(StrEnum):
    GENERATE = "generate"
    EDIT = "edit"
    VARIATION = "variation"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})


class ModerationStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


class Upload(UUIDPrimaryKey, Base):
    """A user-supplied reference image, sanitised and re-encoded before storage."""

    __tablename__ = "uploads"
    __table_args__ = (
        Index("ix_uploads_user_created", "user_id", "created_at"),
        Index("ix_uploads_expires", "expires_at", postgresql_where=text("deleted_at IS NULL")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    storage_key: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    moderation_status: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    deleted_at: Mapped[datetime | None]


class GenerationJob(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("kind IN ('generate', 'edit', 'variation')", name="kind"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint("credit_cost > 0", name="credit_cost_positive"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_range"),
        Index(
            "ix_generation_jobs_queue",
            "next_attempt_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_generation_jobs_leases",
            "lease_expires_at",
            postgresql_where=text("status = 'processing'"),
        ),
        Index("ix_generation_jobs_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    # Private user content: never logged; erased when the job is deleted.
    prompt: Mapped[str | None] = mapped_column(Text)
    style_preset: Mapped[str | None] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(16))
    aspect: Mapped[str] = mapped_column(String(16))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    quality: Mapped[str] = mapped_column(String(16))
    output_format: Mapped[str] = mapped_column(String(8))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    credit_cost: Mapped[int] = mapped_column(Integer)
    estimated_cost_usd_micros: Mapped[int] = mapped_column(BigInteger)
    # Known provider cost plus estimates for attempts whose billing is uncertain (timeouts).
    actual_cost_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    billing_uncertain: Mapped[bool] = mapped_column(default=False, server_default="false")
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None]
    error_code: Mapped[str | None] = mapped_column(String(64))
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL")
    )
    source_image_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_images.id", ondelete="SET NULL", use_alter=True)
    )
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class GenerationJobInput(Base):
    __tablename__ = "generation_job_inputs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("uploads.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)


class GeneratedImage(Base):
    __tablename__ = "generated_images"
    __table_args__ = (Index("ix_generated_images_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    storage_key: Mapped[str] = mapped_column(String(300))
    thumb_key: Mapped[str] = mapped_column(String(300))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    deleted_at: Mapped[datetime | None]
