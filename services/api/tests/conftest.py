import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from ivaas.adapters.http.app import create_app
from ivaas.config.settings import Settings

SERVICE = {"X-IVaaS-Key": "dev-pipeline-key"}


#: removed when the run ends; each client gets a folder of its own inside it
_OBJECTS = tempfile.TemporaryDirectory(prefix="ivaas-tests-")


def fresh_objects_dir() -> str:
    """Where one test app keeps its objects and analysis jobs. Never the default
    /tmp/ivaas-objects: a local dev API keeps its jobs there, and an app reloads every
    job it finds at startup and tries to run it."""
    return tempfile.mkdtemp(dir=_OBJECTS.name)


def make_client(**overrides) -> TestClient:
    overrides.setdefault("objects_dir", fresh_objects_dir())
    settings = Settings(storage="memory", events="memory", **overrides)
    return TestClient(create_app(settings))


def login(client: TestClient, username: str, password: str | None = None) -> dict:
    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password or username}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def anon():
    with make_client() as c:
        yield c


@pytest.fixture
def client(anon):
    """Logged in as admin; ingest/heartbeat calls must add `SERVICE` headers themselves."""
    anon.headers.update(login(anon, "admin"))
    return anon


@pytest.fixture(scope="session")
def postgres_url():
    """A real Postgres with the shipped migrations, shared by every postgres-marked test."""
    if os.environ.get("IVAAS_TEST_DATABASE_URL"):
        yield os.environ["IVAAS_TEST_DATABASE_URL"]
        return
    from testcontainers.community.postgres import PostgresContainer

    # Colima/rootless Docker cannot run the ryuk reaper sidecar; the context manager
    # removes the container itself anyway.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
        host, port = pg.get_container_host_ip(), pg.get_exposed_port(5432)
        yield f"postgresql+asyncpg://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"
