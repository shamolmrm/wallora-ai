import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base, utcnow


class SubjectType(StrEnum):
    PROMPT = "prompt"
    UPLOAD = "upload"
    OUTPUT = "output"


class Decision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


class ModerationCheck(Base):
    """Outcome of one automated moderation check. Stores category names only, never content."""

    __tablename__ = "moderation_checks"
    __table_args__ = (
        CheckConstraint("subject_type IN ('prompt', 'upload', 'output')", name="subject_type"),
        CheckConstraint("decision IN ('allow', 'block')", name="decision"),
        Index("ix_moderation_checks_user_decision", "user_id", "decision", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[uuid.UUID | None]
    provider: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(8))
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
