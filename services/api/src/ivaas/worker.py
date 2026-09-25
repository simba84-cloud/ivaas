"""The video-analysis worker, as its own process.

Analysis is CPU-bound and long: a 36-minute clip saturates every core it is given
for over an hour. Run inside the API, that work competes with the portal's own
requests and jobs can only ever run one at a time. This entrypoint runs the same
use case against the same queue with no HTTP surface, so analysis scales — and can
move to a GPU box — independently of serving.

    python -m ivaas.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from ivaas.application.analysis import job_worker
from ivaas.config.container import build_container
from ivaas.config.settings import Settings

log = logging.getLogger(__name__)


async def run(settings: Settings | None = None, stopping: asyncio.Event | None = None) -> None:
    """Poll for jobs until signalled. `stopping` is injectable so tests can stop it."""
    settings = settings or Settings()
    logging.getLogger("ivaas").setLevel(logging.INFO)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )

    stop = stopping or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # not every platform supports loop signal handlers; the container does
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    container = await build_container(settings)
    log.info("analysis worker ready; polling for jobs")
    task = asyncio.create_task(job_worker(lambda: container.run_next_job))
    try:
        await stop.wait()
        log.info("stopping the analysis worker")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await container.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
