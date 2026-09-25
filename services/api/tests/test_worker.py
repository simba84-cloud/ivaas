"""The analysis worker runs beside the API, not inside it.

A 36-minute clip saturates every core it is given for over an hour. If that runs in
the API process it competes with the portal's requests, so the split has to be real:
the API must not start the worker when it is told not to, and the worker entrypoint
must drain the same queue on its own.
"""

import asyncio

import pytest
from conftest import make_client

from ivaas.config.settings import Settings
from ivaas.worker import run


def _task_names(client) -> list[str]:
    return [t.get_coro().__qualname__ for t in client.app.state.background_tasks]


def test_api_runs_the_worker_in_process_by_default():
    """A single-process deployment must still analyse videos."""
    with make_client() as client:
        assert "job_worker" in _task_names(client)


def test_api_does_not_run_the_worker_when_a_separate_one_is_deployed():
    with make_client(run_analysis_worker=False) as client:
        names = _task_names(client)
        assert "job_worker" not in names
        # the control-plane loops stay: they belong to the API, not the worker
        assert any("sweep_idle_sessions" in n for n in names)
        assert any("refresh_camera_status" in n for n in names)


@pytest.mark.asyncio
async def test_worker_entrypoint_starts_drains_and_shuts_down_cleanly():
    settings = Settings(storage="memory", events="memory", objects="local")
    stopping = asyncio.Event()

    task = asyncio.create_task(run(settings, stopping=stopping))
    await asyncio.sleep(0.2)  # let it build the container and start polling
    assert not task.done(), "worker exited instead of polling for jobs"

    stopping.set()
    await asyncio.wait_for(task, timeout=10)
    assert task.exception() is None
