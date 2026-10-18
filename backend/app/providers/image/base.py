"""Provider-neutral image generation contract.

Workers depend only on this module. Each provider maps its own errors onto
``ProviderError`` so retry and refund decisions are made in one place.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class TokenUsage:
    text_input_tokens: int = 0
    image_input_tokens: int = 0
    image_output_tokens: int = 0
    text_output_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "text_input_tokens": self.text_input_tokens,
            "image_input_tokens": self.image_input_tokens,
            "image_output_tokens": self.image_output_tokens,
            "text_output_tokens": self.text_output_tokens,
        }


@dataclass(frozen=True)
class InputImage:
    data: bytes
    mime_type: str
    filename: str


@dataclass(frozen=True)
class ImageRequest:
    model: str
    prompt: str
    width: int
    height: int
    quality: str
    output_format: str
    safety_identifier: str
    # Empty for text-to-image; one or more images for edits and variations.
    images: list[InputImage] = field(default_factory=list)

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass(frozen=True)
class ImageResult:
    data: bytes
    mime_type: str
    usage: TokenUsage | None
    provider_request_id: str | None = None


class ProviderError(Exception):
    """A provider call failed.

    ``retryable`` decides whether the worker requeues the job.
    ``billing_uncertain`` means the provider may have charged for the attempt
    (for example a timeout after the request was sent), so spend accounting
    keeps the estimate for that attempt.
    """

    def __init__(
        self, code: str, message: str, *, retryable: bool, billing_uncertain: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.billing_uncertain = billing_uncertain


class ContentBlockedError(ProviderError):
    def __init__(self, message: str = "Blocked by the provider's safety system") -> None:
        # Blocked outputs may still be billed; treat billing as uncertain.
        super().__init__("content_blocked", message, retryable=False, billing_uncertain=True)


class ImageProvider(Protocol):
    name: str

    async def generate(self, request: ImageRequest) -> ImageResult: ...

    async def edit(self, request: ImageRequest) -> ImageResult: ...
