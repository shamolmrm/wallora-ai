"""Run the generation worker:  py -m uv run python -m app.worker"""

import asyncio
import contextlib
import logging
import os
import signal
import socket

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.worker.processor import GenerationWorker

logger = logging.getLogger("app.worker")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    worker = GenerationWorker(container, worker_id=worker_id)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler is unavailable on Windows; Ctrl+C then raises KeyboardInterrupt.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    logger.info(
        "worker_started id=%s provider=%s concurrency=%d",
        worker_id,
        container.image_provider.name,
        settings.worker_concurrency,
    )
    try:
        await worker.run_forever(stop)
    finally:
        await container.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
