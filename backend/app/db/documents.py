"""Typed Firestore documents.

Dataclasses hold only Firestore-native values (str, int, bool, datetime, lists,
dicts), so ``asdict`` produces the stored document and ``from_doc`` rebuilds
it. IDs are UUIDv7 strings.
"""

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from typing import Any, Self, cast


class Document:
    def to_doc(self) -> dict[str, Any]:
        return asdict(cast(Any, self))

    @classmethod
    def from_doc(cls, value: dict[str, Any]) -> Self:
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in value.items() if k in known})


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING_DELETION = "pending_deletion"
    DELETED = "deleted"


@dataclass
class UserDoc(Document):
    id: str
    firebase_uid: str
    email: str | None
    email_verified: bool
    status: str
    created_at: datetime
    updated_at: datetime
    held: int = 0
    ledger_seq: int = 0
    # Credit lots live on the user document (lot_id -> LotDoc fields), so a credit
    # transaction reads exactly one document and runs no queries; queries inside
    # transactions lock ranges and collapse under same-user contention.
    credit_lots: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Sliding windows kept on the user document, which every credit transaction
    # already locks: this makes per-user limits exact without extra queries.
    recent_job_times: list[datetime] = field(default_factory=list)
    recent_upload_times: list[datetime] = field(default_factory=list)
    strike_times: list[datetime] = field(default_factory=list)


class LotSource(StrEnum):
    WELCOME = "welcome"
    DAILY_FREE = "daily_free"
    REWARD_AD = "reward_ad"
    PURCHASE = "purchase"
    SUBSCRIPTION = "subscription"
    ADMIN = "admin"
    REFUND = "refund"


@dataclass
class LotDoc(Document):
    id: str
    source: str
    source_ref: str
    amount_granted: int
    amount_remaining: int
    expires_at: datetime | None
    created_at: datetime


class LedgerEntryType(StrEnum):
    GRANT = "grant"
    RESERVE = "reserve"
    CAPTURE = "capture"
    RELEASE = "release"
    EXPIRE = "expire"
    REVOKE = "revoke"
    ADMIN_ADJUST = "admin_adjust"


@dataclass
class LedgerDoc(Document):
    seq: int
    user_id: str
    entry_type: str
    amount: int  # delta on available credits
    held_delta: int
    available_after: int
    held_after: int
    lot_id: str | None
    job_id: str | None
    reason: str | None
    created_at: datetime


class ReservationStatus(StrEnum):
    HELD = "held"
    CAPTURED = "captured"
    RELEASED = "released"


@dataclass
class Allocation(Document):
    lot_id: str
    amount: int


@dataclass
class ReservationDoc(Document):
    amount: int
    status: str
    allocations: list[dict[str, Any]]
    created_at: datetime
    resolved_at: datetime | None = None

    def allocation_list(self) -> list[Allocation]:
        return [Allocation.from_doc(a) for a in self.allocations]


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


@dataclass
class JobDoc(Document):
    id: str
    user_id: str
    idempotency_key: str
    request_hash: str
    kind: str
    status: str
    # Private user content: never logged; erased when the job is deleted.
    prompt: str | None
    style_preset: str | None
    tier: str
    aspect: str
    width: int
    height: int
    quality: str
    output_format: str
    provider: str
    model: str
    credit_cost: int
    estimated_cost_usd_micros: int
    max_attempts: int
    next_attempt_at: datetime
    created_at: datetime
    updated_at: datetime
    reservation: dict[str, Any]
    input_upload_ids: list[str] = field(default_factory=list)
    source_image_id: str | None = None
    retry_of_job_id: str | None = None
    actual_cost_usd_micros: int = 0
    billing_uncertain: bool = False
    usage: dict[str, int] | None = None
    attempts: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deleted_at: datetime | None = None
    visible: bool = True  # False once deleted; lets history queries filter by equality

    def reservation_doc(self) -> ReservationDoc:
        return ReservationDoc.from_doc(self.reservation)


class ModerationStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


@dataclass
class UploadDoc(Document):
    id: str
    user_id: str
    storage_key: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    sha256: str
    moderation_status: str
    expires_at: datetime
    created_at: datetime
    deleted: bool = False
    deleted_at: datetime | None = None


@dataclass
class ImageDoc(Document):
    id: str
    job_id: str
    user_id: str
    storage_key: str
    thumb_key: str
    width: int
    height: int
    mime_type: str
    size_bytes: int
    created_at: datetime
    deleted_at: datetime | None = None


class SubjectType(StrEnum):
    PROMPT = "prompt"
    UPLOAD = "upload"
    OUTPUT = "output"


@dataclass
class ModerationCheckDoc(Document):
    """Outcome of one blocking moderation decision. Category names only, never content."""

    id: str
    user_id: str
    subject_type: str
    subject_id: str | None
    provider: str
    decision: str
    categories: list[str]
    created_at: datetime


class WallpaperStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    REMOVED = "removed"


@dataclass
class CategoryDoc(Document):
    slug: str
    name: str
    sort_order: int
    active: bool = True
    cover_wallpaper_id: str | None = None
    wallpaper_count: int = 0


@dataclass
class WallpaperDoc(Document):
    """A catalog wallpaper. ``source_type`` + ``license`` record provenance for store review."""

    id: str
    title: str
    category: str
    tags: list[str]
    status: str
    source_type: str  # "ai_inhouse" | "original" | "licensed"
    license: str
    width: int
    height: int
    mime_type: str
    storage_key: str
    thumb_key: str
    created_at: datetime
    published_at: datetime | None = None
    featured: bool = False
    is_premium: bool = False
    model: str | None = None
    cost_usd_micros: int = 0
