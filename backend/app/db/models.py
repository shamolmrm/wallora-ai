"""Import every model so ``Base.metadata`` is complete (Alembic, tests)."""

from app.db.base import Base
from app.modules.credits.models import (
    CreditAccount,
    CreditLedgerEntry,
    CreditLot,
    CreditReservation,
    CreditReservationAllocation,
)
from app.modules.moderation.models import ModerationCheck
from app.modules.spend.models import SpendCounter
from app.modules.studio.models import GeneratedImage, GenerationJob, GenerationJobInput, Upload
from app.modules.users.models import User

__all__ = [
    "Base",
    "CreditAccount",
    "CreditLedgerEntry",
    "CreditLot",
    "CreditReservation",
    "CreditReservationAllocation",
    "GeneratedImage",
    "GenerationJob",
    "GenerationJobInput",
    "ModerationCheck",
    "SpendCounter",
    "Upload",
    "User",
]
