import pytest
from fastapi.testclient import TestClient

from ivaas.adapters.http.app import create_app
from ivaas.config.settings import Settings

SERVICE = {"X-IVaaS-Key": "dev-pipeline-key"}


def make_client(**overrides) -> TestClient:
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
