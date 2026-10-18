from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModerationVerdict:
    blocked: bool
    provider: str
    categories: list[str] = field(default_factory=list)


class ModerationUnavailableError(Exception):
    """Moderation could not be performed. Callers fail closed (no generation)."""


class ModerationProvider(Protocol):
    name: str

    async def check_text(self, text: str) -> ModerationVerdict: ...

    async def check_image(self, data: bytes, mime_type: str) -> ModerationVerdict: ...
