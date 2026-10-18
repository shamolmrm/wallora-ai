import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.modules.moderation.models import Decision, ModerationCheck, SubjectType
from app.providers.moderation.base import ModerationProvider, ModerationVerdict
from app.providers.moderation.blocklist import blocklist_categories


async def moderate_prompt(provider: ModerationProvider, prompt: str) -> ModerationVerdict:
    """Local blocklist first (no network), then the moderation model."""
    local = blocklist_categories(prompt)
    if local:
        return ModerationVerdict(blocked=True, provider="blocklist", categories=local)
    return await provider.check_text(prompt)


def record_check(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_type: SubjectType,
    subject_id: uuid.UUID | None,
    verdict: ModerationVerdict,
    now: datetime,
) -> None:
    session.add(
        ModerationCheck(
            id=new_id(),
            user_id=user_id,
            subject_type=subject_type,
            subject_id=subject_id,
            provider=verdict.provider,
            decision=Decision.BLOCK if verdict.blocked else Decision.ALLOW,
            categories=verdict.categories,
            created_at=now,
        )
    )


async def recent_strikes(
    session: AsyncSession, *, user_id: uuid.UUID, now: datetime, window: timedelta
) -> int:
    stmt = select(func.count()).where(
        ModerationCheck.user_id == user_id,
        ModerationCheck.decision == Decision.BLOCK,
        ModerationCheck.created_at > now - window,
    )
    return int((await session.execute(stmt)).scalar_one())
