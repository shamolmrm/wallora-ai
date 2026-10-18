"""Prompt validation and composition."""

import re
import unicodedata

from app.core.errors import ProblemError

MIN_PROMPT_CHARS = 3
DEFAULT_VARIATION_PROMPT = (
    "Create a new variation of this wallpaper that keeps its style, palette and mood."
)
WALLPAPER_SUFFIX = "Compose it as a phone wallpaper. No text, captions, logos or watermarks."

STYLE_PRESETS: dict[str, str] = {
    "cinematic": "Cinematic lighting, rich detail, shallow depth of field.",
    "minimal": "Minimalist composition, clean shapes, generous negative space.",
    "anime": "Anime illustration style, crisp line art, vibrant colours.",
    "fantasy": "Epic fantasy art, painterly detail, atmospheric light.",
    "amoled": "Pure black background, high contrast, small vivid accents.",
    "watercolor": "Soft watercolour painting, textured paper, gentle gradients.",
    "abstract": "Abstract art, flowing forms, bold colour harmony.",
    "photoreal": "Photorealistic, natural light, high dynamic range.",
}

_WHITESPACE = re.compile(r"\s+")


def normalize_prompt(raw: str, *, max_chars: int) -> str:
    text = unicodedata.normalize("NFKC", raw)
    # Replace control and format characters (zero-width, bidi overrides) with spaces.
    text = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) < MIN_PROMPT_CHARS:
        raise ProblemError(422, "prompt_too_short", "The prompt is too short.")
    if len(text) > max_chars:
        raise ProblemError(
            422, "prompt_too_long", f"The prompt must be at most {max_chars} characters."
        )
    return text


def compose_prompt(prompt: str, style_preset: str | None) -> str:
    parts = [prompt]
    if style_preset:
        parts.append(STYLE_PRESETS[style_preset])
    parts.append(WALLPAPER_SUFFIX)
    return " ".join(parts)
