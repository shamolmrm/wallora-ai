import pytest

from app.core.errors import ProblemError
from app.modules.studio.catalog import ASPECTS, TIERS, cost_micros, estimate_cost_micros
from app.modules.studio.prompts import compose_prompt, normalize_prompt
from app.providers.image.base import TokenUsage
from app.providers.image.models import MODELS, get_model, size_problem
from app.providers.moderation.blocklist import blocklist_categories


def test_normalize_prompt_cleans_control_and_whitespace() -> None:
    raw = "  misty\u200b  forest\n\tat\x00dawn  "
    assert normalize_prompt(raw, max_chars=100) == "misty forest at dawn"


def test_normalize_prompt_applies_nfkc() -> None:
    assert (
        normalize_prompt("\uff46\uff55\uff4c\uff4c\uff57\uff49\uff44\uff54\uff48", max_chars=100)
        == "fullwidth"
    )


@pytest.mark.parametrize(
    ("raw", "code"), [(" a ", "prompt_too_short"), ("x" * 11, "prompt_too_long")]
)
def test_normalize_prompt_limits(raw: str, code: str) -> None:
    with pytest.raises(ProblemError) as info:
        normalize_prompt(raw, max_chars=10)
    assert info.value.code == code


def test_compose_prompt_appends_preset_and_wallpaper_guidance() -> None:
    composed = compose_prompt("a fox", "minimal")
    assert composed.startswith("a fox Minimalist")
    assert "No text" in composed


def test_tier_models_exist_and_support_their_quality() -> None:
    for tier in TIERS.values():
        for name in (tier.model, tier.edit_model):
            assert tier.quality in get_model(name).qualities


def test_phone_aspect_is_valid_for_gpt_image_2_family_only() -> None:
    phone = ASPECTS["phone"]
    assert (
        size_problem(
            get_model("gpt-image-2.5-flare"), phone.width, phone.height, allow_experimental=False
        )
        is None
    )
    assert size_problem(
        get_model("gpt-image-1-mini"), phone.width, phone.height, allow_experimental=False
    )


@pytest.mark.parametrize(
    ("width", "height", "fragment"),
    [
        (1000, 2000, "multiples of 16"),
        (512, 2048, "1:3 and 3:1"),
        (4000, 2000, "may not exceed"),
        (512, 512, "total pixels"),
        (1440, 3120, "experimental"),
    ],
)
def test_custom_size_rules(width: int, height: int, fragment: str) -> None:
    problem = size_problem(get_model("gpt-image-2"), width, height, allow_experimental=False)
    assert problem is not None
    assert fragment in problem


def test_experimental_size_allowed_when_enabled() -> None:
    assert size_problem(get_model("gpt-image-2"), 1440, 3120, allow_experimental=True) is None


def test_fixed_sizes_valid_everywhere() -> None:
    for model in MODELS.values():
        assert size_problem(model, 1024, 1536, allow_experimental=False) is None


def test_cost_micros_uses_per_million_rates() -> None:
    # gpt-image-2.5-flare: $5 text in, $8 image in, $30 image out per 1M tokens.
    usage = TokenUsage(text_input_tokens=100, image_input_tokens=1000, image_output_tokens=1584)
    assert cost_micros("gpt-image-2.5-flare", usage) == 100 * 5 + 1000 * 8 + 1584 * 30


def test_estimate_is_conservative_and_scales_with_quality() -> None:
    def estimate(quality: str) -> int:
        return estimate_cost_micros(
            model="gpt-image-2.5-flare",
            quality=quality,
            width=1024,
            height=1536,
            prompt_chars=300,
            input_images=0,
        )

    assert estimate("low") < estimate("medium") < estimate("high")
    # Published gpt-image-1 medium portrait is 1584 output tokens; the estimate must exceed it.
    assert estimate("medium") > 1584 * 30


@pytest.mark.parametrize(
    "prompt",
    ["nude child on a beach", "underage girl, sexy pose", "explicit porn scene"],
)
def test_blocklist_blocks(prompt: str) -> None:
    assert blocklist_categories(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "children playing in autumn leaves",
        "a minor chord rendered as neon waves",
        "kids' toy robot",
    ],
)
def test_blocklist_allows_benign(prompt: str) -> None:
    assert blocklist_categories(prompt) == []
