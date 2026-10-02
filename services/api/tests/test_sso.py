"""M8, T8.2: a tenant sets up Azure AD single sign-on; its people get in, outsiders
do not.

The provider is a fake Azure AD (v2.0 endpoints) behind httpx.MockTransport: real
discovery, a real RSA-signed ID token, a real code exchange with PKCE. Who signs in
there is chosen per test by the code the "browser" brings back.
"""

from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from conftest import login, make_client
from cryptography.hazmat.primitives.asymmetric import rsa
from test_billing_api import USERS

from ivaas.adapters.auth.oidc_client import OidcClient

TID = "9f3a1c2e-0000-4000-8000-bakersinn001"
AZURE = f"https://login.microsoftonline.com/{TID}/v2.0"
CLIENT = "ivaas-bakers-inn"
SECRET = "azure-client-secret-value"
PORTAL = "http://localhost:8088"


class FakeAzure:
    """Discovery, JWKS and a token endpoint. `people[code]` is who signed in."""

    def __init__(self, issuer: str = AZURE) -> None:
        self.issuer = issuer
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.people: dict[str, dict] = {}
        self.issued: dict[str, tuple[str, str]] = {}  # code -> (nonce, challenge)
        self.overrides: dict = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if not url.startswith(self.issuer):
            return httpx.Response(404)  # some other host: nothing there
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/oauth2/authorize",
                    "token_endpoint": f"{self.issuer}/oauth2/token",
                    "jwks_uri": f"{self.issuer}/discovery/keys",
                },
            )
        if url.endswith("/discovery/keys"):
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [jwk | {"kid": "k1", "use": "sig"}]})
        if url.endswith("/oauth2/token"):
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            if form["client_secret"] != [SECRET] or code not in self.people:
                return httpx.Response(400, json={"error": "invalid_grant"})
            nonce, challenge = self.issued[code]
            verifier = form["code_verifier"][0]
            s256 = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            if s256.rstrip(b"=").decode() != challenge:
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "PKCE"}
                )
            now = int(time.time())
            claims = {
                "iss": self.issuer,
                "aud": CLIENT,
                "iat": now,
                "exp": now + 600,
                "nonce": nonce,
                **self.people[code],
                **self.overrides,
            }
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def sso():
    azure = FakeAzure()
    with make_client(local_users=USERS) as c:
        c.app.state.container.oidc = OidcClient(
            httpx.AsyncClient(transport=httpx.MockTransport(azure.handler))
        )
        yield c, azure


def configure(c, who="admin", **over):
    body = {
        "issuer": AZURE,
        "client_id": CLIENT,
        "client_secret": SECRET,
        "domains": ["bakersinn.co.zw"],
        "default_role": "bay_operator",
        **over,
    }
    r = c.put("/api/v1/sso", json=body, headers=login(c, who))
    assert r.status_code == 200, r.text
    return r.json()


def sign_in(c, azure, person: dict, org="bakers-inn"):
    """The browser's round trip: start, the provider, the callback. -> final redirect."""
    start = c.get("/api/v1/auth/sso/start", params={"org": org}, follow_redirects=False)
    assert start.status_code == 302
    to = urlparse(start.headers["location"])
    if not to.netloc.startswith("login.microsoftonline.com"):
        return start.headers["location"]  # refused before the provider
    q = parse_qs(to.query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT]
    assert q["redirect_uri"] == [f"{PORTAL}/api/v1/auth/sso/callback"]
    code = f"code-{len(azure.people)}"
    azure.people[code] = person
    azure.issued[code] = (q["nonce"][0], q["code_challenge"][0])
    back = c.get(
        "/api/v1/auth/sso/callback",
        params={"state": q["state"][0], "code": code},
        follow_redirects=False,
    )
    assert back.status_code == 302
    return back.headers["location"]


def token_from(location: str) -> str:
    assert location.startswith(f"{PORTAL}/auth/sso#token="), location
    return location.split("#token=", 1)[1]


def error_from(location: str) -> str:
    assert "sso_error=" in location, location
    return parse_qs(urlparse(location).query)["sso_error"][0]


def test_t8_2_a_bakers_inn_employee_signs_in_with_azure_ad(sso):
    c, azure = sso
    shown = configure(c)
    assert shown["secret_configured"] and "client_secret" not in shown  # never read back
    assert shown["redirect_uri"] == f"{PORTAL}/api/v1/auth/sso/callback"
    assert shown["sign_in_url"].endswith("/api/v1/auth/sso/start?org=bakers-inn")

    person = {
        "sub": "aad-oid-1",
        "name": "Tendai M",
        "preferred_username": "Tendai.M@BakersInn.co.zw",
    }
    me = c.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_from(sign_in(c, azure, person))}"},
    )
    assert me.status_code == 200
    me = me.json()
    # a newcomer from the organisation, with the role the tenant chose for them
    assert me["subject"] == "tendai.m@bakersinn.co.zw" and me["tenant"]["slug"] == "bakers-inn"
    assert me["roles"] == ["bay_operator"] and not me["must_change_password"]
    # the next sign-in is the same account, matched on the provider's id
    again = token_from(sign_in(c, azure, person))
    assert (
        c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {again}"}).json()["subject"]
        == me["subject"]
    )
    log = c.get("/api/v1/audit", headers=login(c, "owner")).json()
    assert {(e["action"], e["detail"].get("via")) for e in log} >= {
        ("signed_in", "sso"),
        ("sso_configured", None),
    }


def test_t8_2_an_outsider_the_provider_knows_is_denied(sso):
    c, azure = sso
    configure(c)
    # a guest in Bakers Inn's Azure AD, from outside the organisation
    guest = {"sub": "aad-guest", "email": "someone@gmail.com"}
    assert "not in Bakers Inn's organisation" in error_from(sign_in(c, azure, guest))
    # an organisation with no SSO, or none at all: one answer, revealing nothing
    for org in ("isolation-test", "no-such-org"):
        assert "not set up for that organisation" in error_from(sign_in(c, azure, guest, org=org))


def test_a_token_not_from_this_provider_for_this_client_and_this_sign_in_is_refused(sso):
    c, azure = sso
    configure(c)
    person = {"sub": "aad-oid-2", "email": "rudo@bakersinn.co.zw"}
    for bad in (
        {"iss": "https://evil.example/v2.0"},
        {"aud": "another-app"},
        {"nonce": "replayed"},
    ):
        azure.overrides = bad
        assert "sign-in failed" in error_from(sign_in(c, azure, person)), bad
    azure.overrides = {}
    # a callback that was never started here, or used twice, gets nothing
    r = c.get(
        "/api/v1/auth/sso/callback",
        params={"state": "made-up", "code": "x"},
        follow_redirects=False,
    )
    assert "expired or was not started here" in error_from(r.headers["location"])
    # the person cancelled at the provider
    r = c.get(
        "/api/v1/auth/sso/callback",
        params={"error": "access_denied", "error_description": "The user cancelled"},
        follow_redirects=False,
    )
    assert error_from(r.headers["location"]) == "The user cancelled"


def test_accounts_are_matched_on_the_provider_id_and_never_cross_tenants(sso):
    c, azure = sso
    configure(c, default_role=None)  # existing accounts only
    person = {"sub": "aad-oid-3", "email": "chipo@bakersinn.co.zw"}
    assert "ask your administrator to add you" in error_from(sign_in(c, azure, person))
    # the admin adds her by email, and she gets in as what she was given
    r = c.post(
        "/api/v1/users",
        json={
            "username": "chipo@bakersinn.co.zw",
            "display_name": "Chipo",
            "roles": ["site_manager"],
        },
        headers=login(c, "admin"),
    )
    assert r.status_code in (200, 201), r.text
    me = c.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_from(sign_in(c, azure, person))}"},
    ).json()
    assert me["roles"] == ["site_manager"]
    # the address later reassigned to someone else at the provider: not her account
    impostor = {"sub": "aad-oid-other", "email": "chipo@bakersinn.co.zw"}
    assert "linked to a different person" in error_from(sign_in(c, azure, impostor))
    # an address whose account is another tenant's
    other = c.post(
        "/api/v1/users",
        json={"username": "ops@bakersinn.co.zw", "display_name": "Ops", "roles": ["auditor"]},
        headers=login(c, "b-admin"),
    )
    assert other.status_code in (200, 201), other.text
    stranger = {"sub": "aad-oid-4", "email": "ops@bakersinn.co.zw"}
    assert "belongs to another organisation" in error_from(sign_in(c, azure, stranger))


def test_required_sso_turns_passwords_off_except_the_owners(sso):
    c, _ = sso
    configure(c, required=True)
    r = c.post("/api/v1/auth/login", json={"username": "operator", "password": "operator"})
    assert r.status_code == 403 and "single sign-on" in r.json()["detail"]
    # a wrong password still says only that it is wrong
    r = c.post("/api/v1/auth/login", json={"username": "operator", "password": "nope"})
    assert r.status_code == 401
    # the owner keeps a way in if the provider breaks
    assert login(c, "owner")
    removed = c.delete("/api/v1/sso", headers=login(c, "owner")).json()
    assert removed["configured"] is False
    assert login(c, "operator")


def test_only_administrators_set_it_up_and_a_bad_provider_is_refused_on_save(sso):
    c, _ = sso
    body = {
        "issuer": AZURE,
        "client_id": CLIENT,
        "client_secret": SECRET,
        "domains": ["bakersinn.co.zw"],
    }
    assert c.put("/api/v1/sso", json=body, headers=login(c, "operator")).status_code == 403
    assert c.get("/api/v1/sso", headers=login(c, "viewer")).status_code == 403
    r = c.put(
        "/api/v1/sso",
        json={**body, "issuer": "https://login.example.org/nope"},
        headers=login(c, "admin"),
    )
    assert r.status_code == 422 and "could not be reached" in r.json()["detail"]
    r = c.put(
        "/api/v1/sso", json={**body, "default_role": "tenant_admin"}, headers=login(c, "admin")
    )
    assert r.status_code == 422 and "cannot be made" in r.json()["detail"]
    r = c.put("/api/v1/sso", json={**body, "client_secret": ""}, headers=login(c, "admin"))
    assert r.status_code == 422 and "secret is required" in r.json()["detail"]
    configure(c)
    # later edits keep the stored secret when none is sent
    kept = c.put(
        "/api/v1/sso",
        json={**body, "client_secret": "", "required": True},
        headers=login(c, "admin"),
    )
    assert kept.status_code == 200 and kept.json()["secret_configured"] and kept.json()["required"]
    # another tenant sees nothing of it
    assert c.get("/api/v1/sso", headers=login(c, "b-admin")).json()["configured"] is False
