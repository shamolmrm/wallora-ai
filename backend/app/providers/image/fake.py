"""Deterministic, free image provider for development and tests.

Produces a gradient seeded from the prompt hash at the requested size. Tests
can queue scripted outcomes (errors, delays) to exercise retry and refund
paths without any network access.
"""

import asyncio
import hashlib
import io
from collections import deque
from dataclasses import dataclass

from PIL import Image

from app.providers.image.base import ImageRequest, ImageResult, ProviderError, TokenUsage


@dataclass(frozen=True)
class FakeOutcome:
    error: ProviderError | None = None
    delay_seconds: float = 0.0
    usage: TokenUsage | None = None


class FakeImageProvider:
    name = "fake"

    def __init__(self) -> None:
        self._script: deque[FakeOutcome] = deque()
        self.calls: list[tuple[str, ImageRequest]] = []

    def enqueue(self, *outcomes: FakeOutcome) -> None:
        self._script.extend(outcomes)

    async def generate(self, request: ImageRequest) -> ImageResult:
        return await self._run("generate", request)

    async def edit(self, request: ImageRequest) -> ImageResult:
        return await self._run("edit", request)

    async def _run(self, operation: str, request: ImageRequest) -> ImageResult:
        self.calls.append((operation, request))
        outcome = self._script.popleft() if self._script else FakeOutcome()
        if outcome.delay_seconds:
            await asyncio.sleep(outcome.delay_seconds)
        if outcome.error is not None:
            raise outcome.error
        data = await asyncio.to_thread(_render, request)
        usage = outcome.usage or TokenUsage(
            text_input_tokens=max(1, len(request.prompt) // 4),
            image_input_tokens=1000 * len(request.images),
            image_output_tokens=1000,
        )
        return ImageResult(data=data, mime_type="image/webp", usage=usage)


def _render(request: ImageRequest) -> bytes:
    digest = hashlib.sha256(request.prompt.encode()).digest()
    start = (digest[0], digest[1], digest[2])
    end = (digest[3], digest[4], digest[5])
    # Draw a small gradient and upscale: fast, still exactly the requested size.
    small = Image.new("RGB", (16, 64))
    for y in range(64):
        t = y / 63
        color = tuple(round(s + (e - s) * t) for s, e in zip(start, end, strict=True))
        for x in range(16):
            small.putpixel((x, y), color)
    image = small.resize((request.width, request.height), Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=80)
    return buffer.getvalue()
