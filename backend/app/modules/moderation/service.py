from datetime import datetime, timedelta

from google.cloud.firestore_v1 import AsyncClient, AsyncTransaction

from app.core.ids import new_id
from app.db.documents import ModerationCheckDoc, SubjectType, UserDoc
from app.db.firestore import MODERATION_CHECKS
from app.providers.moderation.base import ModerationProvider, ModerationVerdict
from app.providers.moderation.blocklist import blocklist_categories


async def moderate_prompt(provider: ModerationProvider, prompt: str) -> ModerationVerdict:
    """Local blocklist first (no network), then the moderation model."""
    local = blocklist_categories(prompt)
    if local:
        return ModerationVerdict(blocked=True, provider="blocklist", categories=local)
    return await provider.check_text(prompt)


def record_block(
    db: AsyncClient,
    txn: AsyncTransaction,
    *,
    user: UserDoc,
    subject_type: SubjectType,
    subject_id: str | None,
    verdict: ModerationVerdict,
    now: datetime,
) -> None:
    """Stage an audit record and an abuse strike. The caller writes the user document."""
    check = ModerationCheckDoc(
        id=str(new_id()),
        user_id=user.id,
        subject_type=subject_type,
        subject_id=subject_id,
        provider=verdict.provider,
        decision="block",
        categories=verdict.categories,
        created_at=now,
    )
    txn.set(db.collection(MODERATION_CHECKS).document(check.id), check.to_doc())
    user.strike_times.append(now)


def recent_count(times: list[datetime], *, now: datetime, window: timedelta) -> int:
    return sum(1 for t in times if t > now - window)


def prune(times: list[datetime], *, now: datetime, keep: timedelta) -> list[datetime]:
    return [t for t in times if t > now - keep]
