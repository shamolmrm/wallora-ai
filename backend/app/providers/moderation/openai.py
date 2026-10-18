"""OpenAI Moderation API adapter (free endpoint; text and image input).

Docs checked 2026-10-18: https://developers.openai.com/api/docs/guides/moderation
Model ``omni-moderation-latest`` accepts ``{"type": "image_url", "image_url":
{"url": "data:<mime>;base64,..."}}`` parts for images up to 20 MB.
"""

import base64
from typing import Any

import httpx2
from openai import AsyncOpenAI, OpenAIError

from app.providers.moderation.base import ModerationUnavailableError, ModerationVerdict


class OpenAIModerationProvider:
    name = "openai"

    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def create(
        cls,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        organization: str | None = None,
        project: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> "OpenAIModerationProvider":
        client = AsyncOpenAI(
            api_key=api_key,
            organization=organization,
            project=project,
            timeout=timeout_seconds,
            max_retries=2,  # free endpoint: SDK retries are safe here
            http_client=http_client,
        )
        return cls(client, model)

    async def check_text(self, text: str) -> ModerationVerdict:
        return await self._check([{"type": "text", "text": text}])

    async def check_image(self, data: bytes, mime_type: str) -> ModerationVerdict:
        url = f"data:{mime_type};base64,{base64.b64encode(data).decode()}"
        return await self._check([{"type": "image_url", "image_url": {"url": url}}])

    async def _check(self, parts: list[dict[str, Any]]) -> ModerationVerdict:
        try:
            response = await self._client.moderations.create(model=self._model, input=parts)  # type: ignore[arg-type]
        except OpenAIError as exc:
            raise ModerationUnavailableError(type(exc).__name__) from exc
        flagged: set[str] = set()
        blocked = False
        for result in response.results:
            blocked = blocked or result.flagged
            categories = result.categories.model_dump(by_alias=True)
            flagged.update(name for name, hit in categories.items() if hit)
        return ModerationVerdict(blocked=blocked, provider=self.name, categories=sorted(flagged))
