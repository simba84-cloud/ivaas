import time

import httpx
import jwt
import pytest
from conftest import SERVICE, login, make_client
from cryptography.hazmat.primitives.asymmetric import rsa

from ivaas.adapters.auth.jwt_verifiers import LocalTokenVerifier, OidcTokenVerifier
from ivaas.ports.auth import AuthError, Principal, Role


def test_role_hierarchy():
    assert Principal("u", "u", frozenset({Role.ADMIN})).allows(Role.VIEWER)
    assert not Principal("u", "u", frozenset({Role.VIEWER})).allows(Role.OPERATOR)
    assert not Principal("u", "u", frozenset({Role.ADMIN})).allows(
        Role.SERVICE
    )  # service is not "above"
    assert not Principal("s", "s", frozenset({Role.SERVICE})).allows(Role.VIEWER)  # nor "below"
    assert not Principal("u", "u").allows(Role.VIEWER)


def test_every_route_is_closed_to_anonymous(anon):
    bay = "0bc39dce-7ea1-5331-b0dc-4ffcd94bbfd3"
    for method, path in [
        ("GET", "/api/v1/bays"),
        ("GET", "/api/v1/sessions"),
        ("GET", "/api/v1/summary"),
        ("POST", "/api/v1/sessions"),
        ("POST", f"/api/v1/bays/{bay}/cameras"),
        ("POST", "/api/v1/discovery/onvif"),
        ("POST", "/api/v1/ingest/crossings"),
        ("GET", "/api/v1/assistant/status"),
        ("GET", "/api/v1/auth/me"),
    ]:
        r = anon.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)
    assert anon.get("/healthz").status_code == 200  # probes stay open
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed, anon.websocket_connect("/ws/events"):
        pass
    assert closed.value.code == 4401


def test_roles_gate_the_right_routes(anon):
    bay = anon.get("/api/v1/bays", headers=login(anon, "viewer")).json()[0]["id"]
    viewer, operator, admin = (login(anon, u) for u in ("viewer", "operator", "admin"))
    open_body = {"bay_id": bay, "direction": "loading"}
    cam_body = {"name": "x", "role": "overhead", "source_url": "rtsp://10.0.0.1/a"}

    assert anon.post("/api/v1/sessions", json=open_body, headers=viewer).status_code == 403
    assert anon.post("/api/v1/sessions", json=open_body, headers=operator).status_code == 201
    assert (
        anon.post(f"/api/v1/bays/{bay}/cameras", json=cam_body, headers=operator).status_code == 403
    )
    assert anon.post(f"/api/v1/bays/{bay}/cameras", json=cam_body, headers=admin).status_code == 201
    # a human admin cannot impersonate the pipeline, and the pipeline cannot browse
    assert anon.post("/api/v1/ingest/crossings", json={}, headers=admin).status_code == 403
    assert anon.get("/api/v1/sessions", headers=SERVICE).status_code == 403
    assert anon.get("/api/v1/auth/me", headers=SERVICE).json()["roles"] == ["service"]


def test_bad_credentials(anon):
    assert (
        anon.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"}).status_code
        == 401
    )
    assert anon.get("/api/v1/bays", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert anon.get("/api/v1/bays", headers={"X-IVaaS-Key": "wrong"}).status_code == 401


def test_expired_and_foreign_local_tokens_are_rejected():
    v = LocalTokenVerifier("x" * 32)
    other = LocalTokenVerifier("y" * 32)
    token = other.mint("u", "u", [Role.ADMIN])
    with pytest.raises(AuthError):
        import asyncio

        asyncio.run(v.verify(token))
    expired = jwt.encode(
        {"iss": "ivaas-local", "sub": "u", "roles": ["admin"], "exp": int(time.time()) - 10},
        "x" * 32,
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        asyncio.run(v.verify(expired))
    with pytest.raises(ValueError):
        LocalTokenVerifier("short")


def test_oidc_mode_disables_password_login():
    with make_client(auth_mode="oidc") as c:
        assert (
            c.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 404
        )
        assert c.get("/api/v1/auth/config").json()["mode"] == "oidc"


# --- OIDC verifier against a fake provider -----------------------------------------
@pytest.mark.asyncio
async def test_oidc_verifier_validates_rs256_and_handles_key_rotation():
    issuer, aud = "https://idp.example/realms/ivaas", "ivaas-portal"
    keys = {
        kid: rsa.generate_private_key(public_exponent=65537, key_size=2048) for kid in ("k1", "k2")
    }
    published = ["k1"]  # k2 appears only after rotation
    fetches = {"jwks": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"jwks_uri": f"{issuer}/protocol/openid-connect/certs"})
        fetches["jwks"] += 1
        jwks = [
            jwt.algorithms.RSAAlgorithm.to_jwk(keys[k].public_key(), as_dict=True) | {"kid": k}
            for k in published
        ]
        return httpx.Response(200, json={"keys": jwks})

    def mint(kid, **extra):
        claims = {
            "iss": issuer,
            "aud": aud,
            "sub": "42",
            "preferred_username": "sam",
            "realm_access": {"roles": ["operator", "offline_access"]},
            "exp": int(time.time()) + 60,
        } | extra
        return jwt.encode(claims, keys[kid], algorithm="RS256", headers={"kid": kid})

    v = OidcTokenVerifier(
        issuer, aud, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    p = await v.verify(mint("k1"))
    assert (p.subject, p.name, p.roles) == (
        "42",
        "sam",
        frozenset({Role.OPERATOR}),
    )  # unknown roles ignored

    with pytest.raises(AuthError):  # k2 not published yet: refetch once, then refuse
        await v.verify(mint("k2"))
    published.append("k2")
    assert (await v.verify(mint("k2"))).subject == "42"
    with pytest.raises(AuthError):
        await v.verify(mint("k1", aud="someone-else"))
    with pytest.raises(AuthError):
        await v.verify(mint("k1", iss="https://evil.example"))
    assert fetches["jwks"] <= 4
