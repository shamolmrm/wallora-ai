"""Check that local development services are reachable.

Uses only the standard library so it runs before dependencies are installed:

    py -m uv run python scripts/dev_check.py

Reads FIRESTORE_EMULATOR_HOST and REDIS_URL from the environment or
backend/.env. Only host and port are checked; credentials are never printed.
"""

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULTS = {
    "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8085",
    "REDIS_URL": "",
}


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


def host_port(name: str, value: str) -> tuple[str, int]:
    if name == "FIRESTORE_EMULATOR_HOST":  # plain host:port
        host, _, port = value.rpartition(":")
        return host or "127.0.0.1", int(port)
    parts = urlsplit(value)
    return parts.hostname or "localhost", parts.port or 6379


def check(name: str, value: str) -> bool:
    host, port = host_port(name, value)
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
        value = os.environ.get(name, env_file.get(name, default))
        if not value:
            print(f"SKIP  {name}: not set")
            continue
        results.append(check(name, value))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
