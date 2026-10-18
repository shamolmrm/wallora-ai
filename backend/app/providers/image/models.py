"""Static knowledge about supported image models.

Source: OpenAI image generation guide, API reference and pricing page,
checked 2026-10-18:
  https://developers.openai.com/api/docs/guides/image-generation
  https://developers.openai.com/api/reference/resources/images
  https://developers.openai.com/api/docs/pricing
Re-verify before changing defaults or enabling paid generation; models and
prices change often.
"""

from dataclasses import dataclass
from decimal import Decimal

FIXED_SIZES = frozenset({(1024, 1024), (1536, 1024), (1024, 1536)})

# Custom WIDTHxHEIGHT rules for the gpt-image-2 family.
SIZE_MULTIPLE = 16
MAX_EDGE = 3840
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
EXPERIMENTAL_PIXELS = 2560 * 1440
MAX_ASPECT = 3.0


@dataclass(frozen=True)
class ModelPricing:
    """Standard-tier USD per 1M tokens."""

    text_input: Decimal
    image_input: Decimal
    image_output: Decimal


@dataclass(frozen=True)
class ModelCapabilities:
    name: str
    custom_sizes: bool
    qualities: frozenset[str]
    supports_edit: bool
    max_input_images: int
    # input_fidelity values accepted by images.edit; empty means "omit the parameter".
    input_fidelity: frozenset[str]
    pricing: ModelPricing


_BASE_QUALITIES = frozenset({"low", "medium", "high", "auto"})
_EXTENDED_QUALITIES = _BASE_QUALITIES | {"xhigh", "max"}
_PRICE_2X = ModelPricing(Decimal("5.00"), Decimal("8.00"), Decimal("30.00"))

MODELS: dict[str, ModelCapabilities] = {
    "gpt-image-2.5-flare": ModelCapabilities(
        "gpt-image-2.5-flare", True, _EXTENDED_QUALITIES, True, 16, frozenset(), _PRICE_2X
    ),
    "gpt-image-2.5-sunburst": ModelCapabilities(
        "gpt-image-2.5-sunburst", True, _EXTENDED_QUALITIES, True, 16, frozenset(), _PRICE_2X
    ),
    "gpt-image-2": ModelCapabilities(
        "gpt-image-2", True, _BASE_QUALITIES, True, 16, frozenset(), _PRICE_2X
    ),
    "gpt-image-1.5": ModelCapabilities(
        "gpt-image-1.5",
        False,
        _BASE_QUALITIES,
        True,
        16,
        frozenset({"low", "high"}),
        ModelPricing(Decimal("5.00"), Decimal("8.00"), Decimal("32.00")),
    ),
    "gpt-image-1-mini": ModelCapabilities(
        "gpt-image-1-mini",
        False,
        _BASE_QUALITIES,
        True,
        16,
        frozenset({"low"}),
        ModelPricing(Decimal("2.00"), Decimal("2.50"), Decimal("8.00")),
    ),
}


def get_model(name: str) -> ModelCapabilities:
    try:
        return MODELS[name]
    except KeyError:
        raise ValueError(f"Unsupported image model: {name}") from None


def size_problem(
    model: ModelCapabilities, width: int, height: int, *, allow_experimental: bool
) -> str | None:
    """Return why ``width``x``height`` is invalid for ``model``, or None if it is valid."""
    if (width, height) in FIXED_SIZES:
        return None
    if not model.custom_sizes:
        return f"{model.name} only supports {sorted(FIXED_SIZES)}"
    if width % SIZE_MULTIPLE or height % SIZE_MULTIPLE:
        return f"both edges must be multiples of {SIZE_MULTIPLE}"
    if max(width, height) > MAX_EDGE:
        return f"edges may not exceed {MAX_EDGE}px"
    if max(width, height) / min(width, height) > MAX_ASPECT:
        return "aspect ratio must be between 1:3 and 3:1"
    pixels = width * height
    if not MIN_PIXELS <= pixels <= MAX_PIXELS:
        return f"total pixels must be between {MIN_PIXELS} and {MAX_PIXELS}"
    if pixels > EXPERIMENTAL_PIXELS and not allow_experimental:
        return "sizes above 2560x1440 are experimental and disabled"
    return None
