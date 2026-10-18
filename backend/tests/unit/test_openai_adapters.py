"""OpenAI adapters exercised through the real SDK with a mocked HTTP transport.

No request leaves the process: every call is answered by ``httpx2.MockTransport``.
"""

import base64
import io
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from PIL import Image

from app.providers.image.base import ContentBlockedError, ImageRequest, InputImage, ProviderError
from app.providers.image.openai import OpenAIImageProvider
from app.providers.moderation.base import ModerationUnavailableError
from app.providers.moderation.openai import OpenAIModerationProvider

Handler = Callable[[httpx2.Request], httpx2.Response]


def _webp() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1072, 2320), (10, 20, 30)).save(buffer, format="WEBP")
    return buffer.getvalue()


def _images_response(usage: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "created": 1_760_000_000,
        "data": [{"b64_json": base64.b64encode(_webp()).decode()}],
        "output_format": "webp",
        "size": "1072x2320",
        "quality": "medium",
    }
    if usage:
        body["usage"] = {
            "input_tokens": 60,
            "input_tokens_details": {"text_tokens": 50, "image_tokens": 10},
            "output_tokens": 2500,
            "output_tokens_details": {"image_tokens": 2500, "text_tokens": 0},
            "total_tokens": 2560,
        }
    return body


def _provider(handler: Handler) -> OpenAIImageProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OpenAIImageProvider.create(api_key="test-key", timeout_seconds=5, http_client=client)


def _request(**overrides: Any) -> ImageRequest:
    values: dict[str, Any] = {
        "model": "gpt-image-2.5-flare",
        "prompt": "misty forest",
        "width": 1072,
        "height": 2320,
        "quality": "medium",
        "output_format": "webp",
        "safety_identifier": "abc123",
    }
    values.update(overrides)
    return ImageRequest(**values)


def _error(status: int, code: str | None = None) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, json={"error": {"message": "nope", "type": "error", "code": code}}
        )

    return handler


async def test_generate_sends_documented_parameters_and_parses_usage() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_images_response())

    result = await _provider(handler).generate(_request())

    request = seen[0]
    assert request.url.path == "/v1/images/generations"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body == {
        "model": "gpt-image-2.5-flare",
        "prompt": "misty forest",
        "size": "1072x2320",
        "quality": "medium",
        "n": 1,
        "output_format": "webp",
        "output_compression": 95,
        "moderation": "auto",
        "safety_identifier": "abc123",
    }
    assert "response_format" not in body and "user" not in body
    assert result.mime_type == "image/webp"
    assert result.data == _webp()
    assert result.usage is not None
    assert result.usage.text_input_tokens == 50
    assert result.usage.image_output_tokens == 2500


async def test_generate_without_usage_returns_none_usage() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_images_response(usage=False))

    result = await _provider(handler).generate(_request())
    assert result.usage is None


async def test_edit_uploads_images_as_multipart() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_images_response())

    images = [
        InputImage(b"\x89PNG-one", "image/png", "input-0.png"),
        InputImage(b"\x89PNG-two", "image/png", "input-1.png"),
    ]
    await _provider(handler).edit(_request(model="gpt-image-2.5-sunburst", images=images))

    request = seen[0]
    assert request.url.path == "/v1/images/edits"
    assert request.headers["content-type"].startswith("multipart/form-data")
    content = request.content
    assert b"input-0.png" in content and b"input-1.png" in content
    assert b"PNG-one" in content and b"PNG-two" in content
    assert b'name="safety_identifier"' in content
    # input_fidelity must be omitted for the gpt-image-2 family.
    assert b"input_fidelity" not in content


async def test_edit_sends_input_fidelity_only_where_supported() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_images_response())

    images = [InputImage(b"\x89PNG", "image/png", "input-0.png")]
    await _provider(handler).edit(
        _request(model="gpt-image-1-mini", width=1024, height=1536, quality="low", images=images)
    )
    assert b'name="input_fidelity"' in seen[0].content
    assert b"\r\n\r\nlow\r\n" in seen[0].content


@pytest.mark.parametrize(
    ("handler", "code", "retryable"),
    [
        (_error(400, "moderation_blocked"), "content_blocked", False),
        (_error(400, "invalid_value"), "provider_rejected", False),
        (_error(401), "provider_misconfigured", False),
        (_error(429, "rate_limit_exceeded"), "rate_limited", True),
        (_error(429, "insufficient_quota"), "provider_quota_exhausted", False),
        (_error(500), "provider_unavailable", True),
        (_error(503), "provider_unavailable", True),
    ],
)
async def test_error_mapping(handler: Handler, code: str, retryable: bool) -> None:
    with pytest.raises(ProviderError) as info:
        await _provider(handler).generate(_request())
    assert info.value.code == code
    assert info.value.retryable is retryable


async def test_content_blocked_is_specific_type() -> None:
    with pytest.raises(ContentBlockedError):
        await _provider(_error(400, "moderation_blocked")).generate(_request())


async def test_timeout_is_retryable_and_billing_uncertain() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError) as info:
        await _provider(handler).generate(_request())
    assert info.value.code == "timeout"
    assert info.value.retryable and info.value.billing_uncertain


async def test_empty_response_is_retryable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"created": 1, "data": []})

    with pytest.raises(ProviderError) as info:
        await _provider(handler).generate(_request())
    assert info.value.code == "empty_response"
    assert info.value.retryable


def _moderation(handler: Handler) -> OpenAIModerationProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return OpenAIModerationProvider.create(
        api_key="test-key", model="omni-moderation-latest", timeout_seconds=5, http_client=client
    )


def _moderation_body(flagged: bool) -> dict[str, Any]:
    names = [
        "sexual", "sexual/minors", "harassment", "harassment/threatening", "hate",
        "hate/threatening", "illicit", "illicit/violent", "self-harm", "self-harm/intent",
        "self-harm/instructions", "violence", "violence/graphic",
    ]  # fmt: skip
    categories = {name: flagged and name == "violence/graphic" for name in names}
    return {
        "id": "modr-1",
        "model": "omni-moderation-latest",
        "results": [
            {
                "flagged": flagged,
                "categories": categories,
                "category_scores": {name: 0.9 if hit else 0.01 for name, hit in categories.items()},
                "category_applied_input_types": {name: ["image"] for name in names},
            }
        ],
    }


async def test_moderation_image_sends_data_url_and_parses_flags() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_moderation_body(flagged=True))

    verdict = await _moderation(handler).check_image(b"\x89PNG", "image/png")

    body = json.loads(seen[0].content)
    assert body["model"] == "omni-moderation-latest"
    assert body["input"][0]["type"] == "image_url"
    assert body["input"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert verdict.blocked
    assert verdict.categories == ["violence/graphic"]


async def test_moderation_text_allows_clean_content() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_moderation_body(flagged=False))

    verdict = await _moderation(handler).check_text("a calm lake")
    assert not verdict.blocked
    assert verdict.categories == []


async def test_moderation_outage_raises_unavailable() -> None:
    with pytest.raises(ModerationUnavailableError):
        await _moderation(_error(500)).check_text("a calm lake")
