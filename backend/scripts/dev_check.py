"""Check that local development services (PostgreSQL, Redis) are reachable.

Uses only the standard library so it runs before dependencies are installed:

    uv run python scripts/dev_check.py

Reads DATABASE_URL and REDIS_URL from the environment or backend/.env.
Only host and port are checked; credentials are never printed.
"""

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://localhost:5432/wallora",
    "REDIS_URL": "redis://localhost:6379/0",
}
DEFAULT_PORTS = {"DATABASE_URL": 5432, "REDIS_URL": 6379}


def load_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split(" #", 1)[0].strip()
    return values


def check(name: str, url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    port = parts.port or DEFAULT_PORTS[name]
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
    except OSError as exc:
        print(f"FAIL  {name}: cannot reach {host}:{port} ({exc.strerror or exc})")
        return False
    print(f"OK    {name}: {host}:{port} is accepting connections")
    return True


def main() -> int:
    env_file = load_env_file()
    results = []
    for name, default in DEFAULTS.items():
        url = os.environ.get(name, env_file.get(name, default))
        if not url:
            print(f"SKIP  {name}: set to empty (in-memory fallback)")
            continue
        results.append(check(name, url))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
