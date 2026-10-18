"""OpenAI Images API provider (``images.generate`` / ``images.edit``).

Built against openai-python 3.x and the API reference checked 2026-10-18.
Notes from the docs that shape this adapter:

* GPT image models always return base64 (``b64_json``); ``response_format`` is
  legacy and not sent.
* ``safety_identifier`` replaces the deprecated ``user`` parameter.
* The variations endpoint is retired; variations are edits of a source image.
* ``usage`` is optional in the response schema, so cost accounting must cope
  with it being absent.
* SDK retries are disabled (``max_retries=0``): the worker owns retries so that
  every attempt is counted against spend limits.
"""

import base64
import logging
from typing import Any

import httpx2
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from openai.types import ImagesResponse

from app.providers.image.base import (
    ContentBlockedError,
    ImageRequest,
    ImageResult,
    ProviderError,
    TokenUsage,
)
from app.providers.image.models import get_model

logger = logging.getLogger(__name__)

OUTPUT_COMPRESSION = 95
HTTP_SERVER_ERROR = 500
_MIME = {"webp": "image/webp", "png": "image/png", "jpeg": "image/jpeg"}


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    @classmethod
    def create(
        cls,
        *,
        api_key: str,
        timeout_seconds: float,
        organization: str | None = None,
        project: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> "OpenAIImageProvider":
        client = AsyncOpenAI(
            api_key=api_key,
            organization=organization,
            project=project,
            timeout=timeout_seconds,
            max_retries=0,
            http_client=http_client,
        )
        return cls(client)

    async def generate(self, request: ImageRequest) -> ImageResult:
        params = self._common_params(request)
        try:
            response = await self._client.images.generate(**params)
        except Exception as exc:
            raise _map_error(exc) from exc
        return _to_result(response, request.output_format)

    async def edit(self, request: ImageRequest) -> ImageResult:
        if not request.images:
            raise ProviderError("invalid_request", "edit requires input images", retryable=False)
        model = get_model(request.model)
        params = self._common_params(request)
        # openai-python 3.35's images.edit() has no `moderation` argument although the API
        # reference lists one; the documented default is "auto", so omitting it is equivalent.
        params.pop("moderation")
        params["image"] = [(img.filename, img.data, img.mime_type) for img in request.images]
        if model.input_fidelity:
            # Only send input_fidelity where the model accepts it; "high" when available.
            params["input_fidelity"] = "high" if "high" in model.input_fidelity else "low"
        try:
            response = await self._client.images.edit(**params)
        except Exception as exc:
            raise _map_error(exc) from exc
        return _to_result(response, request.output_format)

    @staticmethod
    def _common_params(request: ImageRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "prompt": request.prompt,
            "size": request.size,
            "quality": request.quality,
            "n": 1,
            "output_format": request.output_format,
            "moderation": "auto",
            "safety_identifier": request.safety_identifier,
        }
        if request.output_format in ("webp", "jpeg"):
            params["output_compression"] = OUTPUT_COMPRESSION
        return params


def _to_result(response: ImagesResponse, output_format: str) -> ImageResult:
    if not response.data or not response.data[0].b64_json:
        raise ProviderError("empty_response", "provider returned no image", retryable=True)
    try:
        data = base64.b64decode(response.data[0].b64_json, validate=True)
    except ValueError as exc:
        raise ProviderError(
            "invalid_response", "provider returned invalid base64", retryable=True
        ) from exc
    usage = None
    if response.usage is not None:
        u = response.usage
        out_details = u.output_tokens_details
        usage = TokenUsage(
            text_input_tokens=u.input_tokens_details.text_tokens or 0,
            image_input_tokens=u.input_tokens_details.image_tokens or 0,
            image_output_tokens=(out_details.image_tokens or 0) if out_details else u.output_tokens,
            text_output_tokens=(out_details.text_tokens or 0) if out_details else 0,
        )
    fmt = response.output_format or output_format
    return ImageResult(data=data, mime_type=_MIME.get(fmt, "image/png"), usage=usage)


def _error_code(exc: APIStatusError) -> str | None:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            code = error.get("code")
            return code if isinstance(code, str) else None
    return getattr(exc, "code", None)


def _map_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, APITimeoutError):
        return ProviderError(
            "timeout", "provider timed out", retryable=True, billing_uncertain=True
        )
    if isinstance(exc, APIConnectionError):
        return ProviderError(
            "connection_error", "could not reach provider", retryable=True, billing_uncertain=True
        )
    if isinstance(exc, RateLimitError):
        if _error_code(exc) == "insufficient_quota":
            logger.error("openai_insufficient_quota")
            return ProviderError("provider_quota_exhausted", "provider quota", retryable=False)
        return ProviderError("rate_limited", "provider rate limit", retryable=True)
    if isinstance(exc, BadRequestError):
        # Community reports (not the official reference) show safety refusals as
        # HTTP 400 with code "moderation_blocked". Any other 400 is non-retryable too.
        if _error_code(exc) == "moderation_blocked":
            return ContentBlockedError()
        return ProviderError("provider_rejected", "provider rejected the request", retryable=False)
    if isinstance(exc, AuthenticationError | PermissionDeniedError):
        logger.error("openai_auth_failure", extra={"status": exc.status_code})
        return ProviderError("provider_misconfigured", "provider auth failed", retryable=False)
    if isinstance(exc, InternalServerError):
        return ProviderError(
            "provider_unavailable", "provider error", retryable=True, billing_uncertain=True
        )
    if isinstance(exc, APIStatusError):
        retryable = exc.status_code in (408, 409) or exc.status_code >= HTTP_SERVER_ERROR
        return ProviderError(f"http_{exc.status_code}", "provider error", retryable=retryable)
    return ProviderError("unexpected_error", type(exc).__name__, retryable=False)
