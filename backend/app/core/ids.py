"""Time-ordered UUIDv7 identifiers (RFC 9562).

Python 3.14 ships ``uuid.uuid7``. The fallback keeps Python 3.13 (the Docker
target) working with the same layout: 48-bit Unix milliseconds, version 7,
variant bits and 74 random bits.
"""

import os
import time
import uuid
from collections.abc import Callable

_builtin: Callable[[], uuid.UUID] | None = getattr(uuid, "uuid7", None)


def _fallback_uuid7() -> uuid.UUID:
    unix_ms = (time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits; 74 are used
    rand_a = rand & 0xFFF
    rand_b = (rand >> 12) & ((1 << 62) - 1)
    value = (unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def new_id() -> uuid.UUID:
    return _builtin() if _builtin is not None else _fallback_uuid7()
