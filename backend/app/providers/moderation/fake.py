"""Offline moderation for development and tests.

Blocks text containing ``[[block]]`` and images whose bytes contain the same
marker; ``fail_next`` simulates an outage.
"""

from app.providers.moderation.base import ModerationUnavailableError, ModerationVerdict

BLOCK_MARKER = "[[block]]"


class FakeModerationProvider:
    name = "fake"

    def __init__(self) -> None:
        self.fail_next = 0
        self.block_images = False
        self.text_calls = 0
        self.image_calls = 0

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ModerationUnavailableError("simulated outage")

    async def check_text(self, text: str) -> ModerationVerdict:
        self.text_calls += 1
        self._maybe_fail()
        blocked = BLOCK_MARKER in text.lower()
        return ModerationVerdict(blocked, self.name, ["test/blocked"] if blocked else [])

    async def check_image(self, data: bytes, mime_type: str) -> ModerationVerdict:
        self.image_calls += 1
        self._maybe_fail()
        blocked = self.block_images or BLOCK_MARKER.encode() in data
        return ModerationVerdict(blocked, self.name, ["test/blocked"] if blocked else [])
