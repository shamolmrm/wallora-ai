"""Local prompt pre-filter.

A deliberately small, high-precision list that runs before any network call.
It is a cheap first layer, not a substitute for the moderation model, provider
safety systems or human review.
"""

import re

_MINOR_TERMS = (
    r"(child|children|kid|kids|minor|minors|underage|under-age|preteen|pre-teen"
    r"|loli|shota|toddler|schoolgirl|schoolboy)"
)
_SEXUAL_TERMS = r"(nude|naked|nsfw|sexual|sexy|porn|pornographic|erotic|explicit|lingerie|topless)"

_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "sexual/minors",
        re.compile(
            rf"\b{_MINOR_TERMS}\b.*\b{_SEXUAL_TERMS}\b|\b{_SEXUAL_TERMS}\b.*\b{_MINOR_TERMS}\b"
        ),
    ),
    ("sexual", re.compile(r"\b(porn|pornographic|hentai|xxx)\b")),
]


def blocklist_categories(normalized_prompt: str) -> list[str]:
    text = normalized_prompt.lower()
    return [category for category, pattern in _RULES if pattern.search(text)]
