import os
import tempfile

import pytest
from contract import CONTRACT
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
    overrides.setdefault("storage", "memory")
    settings = Settings(events="memory", **overrides)
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


# --- T6.3: every response held against the OpenAPI spec (see contract.py) -------------
_request = TestClient.request


def _checked_request(self, method, url, **kwargs):
    response = _request(self, method, url, **kwargs)
    broken = CONTRACT.check(self.app, response)
    assert not broken, "the response breaks the OpenAPI spec:\n" + "\n".join(broken)
    return response


TestClient.request = _checked_request


def _whole_suite(config) -> bool:
    """Coverage means something only when every test ran: no -k, no chosen files, and
    at most the Postgres tests left out (they exercise repositories, not routes)."""
    chosen = [a for a in config.args if not a.rstrip("/").endswith("tests")]
    marks = (config.option.markexpr or "").replace(" ", "")
    return not config.option.keyword and not chosen and marks in ("", "notpostgres")


#: Operations that can succeed only on the Postgres store: they read every tenant table
#: from the catalogue (export, purge). The in-memory app answers them 501, so a run
#: without the Postgres tests cannot cover them; a whole run must.
POSTGRES_ONLY = {
    ("GET", "/api/v1/account/export"),
    ("POST", "/api/v1/platform/tenants/{tenant_id}/purge"),
}


def pytest_sessionfinish(session, exitstatus):
    if exitstatus != 0 or not _whole_suite(session.config):
        return
    missing = CONTRACT.uncovered()
    if (session.config.option.markexpr or "").replace(" ", "") == "notpostgres":
        missing = [m for m in missing if m not in POSTGRES_ONLY]
    if missing:
        lines = "\n".join(f"  {m} {p}" for m, p in missing)
        session.config.get_terminal_writer().line(
            f"\nT6.3: {len(missing)} operation(s) in the OpenAPI spec never returned a checked "
            f"success in any test:\n{lines}",
            red=True,
        )
        session.exitstatus = 1
