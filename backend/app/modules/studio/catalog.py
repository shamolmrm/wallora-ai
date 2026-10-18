"""Generation tiers, aspect presets and cost estimation.

These defaults mirror docs/08-monetization-and-cost-control.md and are meant
to move into admin-editable ``app_settings`` in the admin phase. Credit prices
are placeholders until real provider cost is measured.

UNVERIFIED: ``OUTPUT_TOKENS_PER_MEGAPIXEL`` is extrapolated from published
gpt-image-1 token counts (1024x1536: low 408, medium 1584, high 6240 tokens).
The 2.5 models' real counts are unknown until an owner-approved test batch is
run. ``ESTIMATE_SAFETY_FACTOR`` keeps estimates conservative so spend caps
trip early rather than late.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from app.providers.image.base import TokenUsage
from app.providers.image.models import get_model


@dataclass(frozen=True)
class Tier:
    name: str
    model: str
    edit_model: str
    quality: str
    credits: int
    premium_only: bool = False


@dataclass(frozen=True)
class Aspect:
    name: str
    width: int
    height: int
    experimental: bool = False


TIERS: dict[str, Tier] = {
    "draft": Tier("draft", "gpt-image-1-mini", "gpt-image-1-mini", "low", 1),
    "standard": Tier("standard", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "medium", 3),
    "hd": Tier("hd", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "high", 8, premium_only=True),
}

ASPECTS: dict[str, Aspect] = {
    "portrait": Aspect("portrait", 1024, 1536),
    "square": Aspect("square", 1024, 1024),
    "landscape": Aspect("landscape", 1536, 1024),
    "phone": Aspect("phone", 1072, 2320),  # ~9:19.5, custom size (gpt-image-2 family)
    "phone_hd": Aspect("phone_hd", 1440, 3120, experimental=True),
}

OUTPUT_TOKENS_PER_MEGAPIXEL = {
    "low": 260,
    "medium": 1010,
    "high": 3970,
    "xhigh": 6000,
    "max": 8000,
    "auto": 3970,
}
ESTIMATE_SAFETY_FACTOR = Decimal("1.5")
IMAGE_INPUT_TOKENS_PER_IMAGE = 2000  # conservative placeholder per reference image
CHARS_PER_TEXT_TOKEN = 3


def cost_micros(model: str, usage: TokenUsage) -> int:
    """Cost in micro-USD. USD per 1M tokens x tokens == micro-USD."""
    pricing = get_model(model).pricing
    total = (
        pricing.text_input * usage.text_input_tokens
        + pricing.image_input * usage.image_input_tokens
        # Text output tokens are rare for image models; charge them at the image-output
        # rate so the figure errs high.
        + pricing.image_output * (usage.image_output_tokens + usage.text_output_tokens)
    )
    return math.ceil(total)


def estimate_usage(
    *, quality: str, width: int, height: int, prompt_chars: int, input_images: int
) -> TokenUsage:
    megapixels = Decimal(width * height) / Decimal(1_000_000)
    per_mp = Decimal(OUTPUT_TOKENS_PER_MEGAPIXEL.get(quality, OUTPUT_TOKENS_PER_MEGAPIXEL["max"]))
    output_tokens = math.ceil(megapixels * per_mp * ESTIMATE_SAFETY_FACTOR)
    return TokenUsage(
        text_input_tokens=math.ceil(prompt_chars / CHARS_PER_TEXT_TOKEN),
        image_input_tokens=IMAGE_INPUT_TOKENS_PER_IMAGE * input_images,
        image_output_tokens=output_tokens,
    )


def estimate_cost_micros(
    *, model: str, quality: str, width: int, height: int, prompt_chars: int, input_images: int
) -> int:
    usage = estimate_usage(
        quality=quality,
        width=width,
        height=height,
        prompt_chars=prompt_chars,
        input_images=input_images,
    )
    return cost_micros(model, usage)
