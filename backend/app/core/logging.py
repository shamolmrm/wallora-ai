"""Logging setup with secret redaction.

Prompts are never passed to loggers by application code. This filter is a
second line of defence that scrubs bearer tokens and API-key-shaped strings
that might end up in exception messages.
"""

import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "sk-[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key|secret|password|token)=([^\s&]+)"), r"\1=[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
