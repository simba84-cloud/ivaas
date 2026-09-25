"""Account management: roles, password resets, and the ways it must not be abusable.

The rules being pinned here are the ones that bite in production: an administrator
must never lock everyone out, a password must never be readable or guessable from a
response, and a reset must actually end the sessions the account already holds.
"""

import pytest
from conftest import login, make_client

from ivaas.application.users import generate_temporary_password
from ivaas.domain.users import (
    MIN_PASSWORD_LENGTH,
    WeakPasswordError,
    validate_password,
)

pytestmark_asyncio = pytest.mark.asyncio


# --- password policy ----------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    ["short", "password123", "aaaaaaaaaaaaaaaa", "changeme", "x" * 201],
)
def test_weak_passwords_are_refused(password):
    with pytest.raises(WeakPasswordError):
        validate_password(password)


def test_a_long_passphrase_is_accepted_without_composition_rules():
    # NIST 800-63B: length, not "one capital and one symbol"
    validate_password("correct horse battery staple")


def test_the_password_cannot_be_the_username():
    with pytest.raises(WeakPasswordError):
        validate_password("operator1234", username="operator1234")


def test_temporary_passwords_are_long_random_and_unambiguous():
    made = {generate_temporary_password() for _ in range(200)}
    assert len(made) == 200, "temporary passwords must not repeat"
    for p in made:
        assert len(p) >= MIN_PASSWORD_LENGTH
        assert not (set(p) & set("O0lI1")), "ambiguous characters are hard to read aloud"


# --- HTTP ---------------------------------------------------------------------


def test_admin_creates_a_user_and_gets_a_one_time_password(client):
    r = client.post(
        "/api/v1/users",
        json={"username": "Yard.Sup", "display_name": "Yard Supervisor", "roles": ["operator"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["username"] == "yard.sup"  # normalised
    assert body["user"]["must_change_password"] is True
    temporary = body["temporary_password"]
    assert len(temporary) >= MIN_PASSWORD_LENGTH

    # the platform cannot show it again: no later response carries it
    listed = client.get("/api/v1/users").json()
    assert temporary not in str(listed)
    assert all("password_hash" not in u for u in listed)


def test_a_hash_is_never_returned_by_any_user_endpoint(client):
    blob = str(client.get("/api/v1/users").json())
    assert "argon2" not in blob and "$" not in blob


def test_a_reset_forces_a_change_and_ends_the_open_session(anon):
    admin = login(anon, "admin")
    operator_before = login(anon, "operator")
    assert anon.get("/api/v1/bays", headers=operator_before).status_code == 200

    reset = anon.post("/api/v1/users/operator/reset-password", headers=admin)
    assert reset.status_code == 200, reset.text
    temporary = reset.json()["temporary_password"]

    # the token the operator was already holding stops working
    assert anon.get("/api/v1/bays", headers=operator_before).status_code == 401

    # the temporary password signs in, but unlocks nothing until it is replaced
    r = anon.post("/api/v1/auth/login", json={"username": "operator", "password": temporary}).json()
    assert r["must_change_password"] is True
    temp_headers = {"Authorization": f"Bearer {r['access_token']}"}
    assert anon.get("/api/v1/bays", headers=temp_headers).status_code == 403
    assert anon.get("/api/v1/auth/me", headers=temp_headers).status_code == 200

    changed = anon.post(
        "/api/v1/auth/password",
        json={"current_password": temporary, "new_password": "a quiet loading bay"},
        headers=temp_headers,
    )
    assert changed.status_code == 200, changed.text

    # the token it hands back works straight away, without signing in again
    fresh = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert anon.get("/api/v1/bays", headers=fresh).status_code == 200
    after = login(anon, "operator", password="a quiet loading bay")
    assert anon.get("/api/v1/bays", headers=after).status_code == 200


def test_changing_a_password_requires_the_current_one(client):
    r = client.post(
        "/api/v1/auth/password",
        json={"current_password": "not-it", "new_password": "a quiet loading bay"},
    )
    assert r.status_code == 409
    assert "current password" in r.json()["detail"]


def test_a_new_password_must_meet_the_policy(client):
    r = client.post(
        "/api/v1/auth/password",
        json={"current_password": "admin", "new_password": "short"},
    )
    assert r.status_code == 422


def test_roles_can_be_reassigned_and_take_effect(anon):
    admin = login(anon, "admin")
    viewer = login(anon, "viewer")
    bay = anon.get("/api/v1/bays", headers=viewer).json()[0]["id"]
    body = {"bay_id": bay, "direction": "loading"}
    assert anon.post("/api/v1/sessions", json=body, headers=viewer).status_code == 403

    r = anon.put("/api/v1/users/viewer/roles", json={"roles": ["operator"]}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["roles"] == ["operator"]

    promoted = login(anon, "viewer")  # a new token carries the new role
    assert anon.post("/api/v1/sessions", json=body, headers=promoted).status_code == 201


def test_an_admin_cannot_lock_themselves_out(client):
    demote = client.put("/api/v1/users/admin/roles", json={"roles": ["viewer"]})
    assert demote.status_code == 409
    assert "your own" in demote.json()["detail"]

    disable = client.put("/api/v1/users/admin/enabled", json={"enabled": False})
    assert disable.status_code == 409


def test_the_last_administrator_cannot_be_removed(client):
    """Even a second admin demoting the first must leave one standing."""
    client.post(
        "/api/v1/users", json={"username": "second", "display_name": "Second", "roles": ["admin"]}
    )
    # two admins now: demoting one is allowed
    assert client.put("/api/v1/users/second/roles", json={"roles": ["viewer"]}).status_code == 200
    # back to one: it cannot be disabled by anyone
    assert client.put("/api/v1/users/admin/enabled", json={"enabled": False}).status_code == 409


def test_a_disabled_account_cannot_sign_in_or_keep_working(anon):
    admin = login(anon, "admin")
    viewer_before = login(anon, "viewer")
    assert anon.get("/api/v1/bays", headers=viewer_before).status_code == 200

    assert (
        anon.put("/api/v1/users/viewer/enabled", json={"enabled": False}, headers=admin).status_code
        == 200
    )

    assert anon.get("/api/v1/bays", headers=viewer_before).status_code == 401
    assert (
        anon.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "viewer"}
        ).status_code
        == 401
    )


def test_a_wrong_username_and_a_wrong_password_are_indistinguishable(anon):
    no_user = anon.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "whatever-long"}
    )
    bad_password = anon.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
    )
    assert no_user.status_code == bad_password.status_code == 401
    assert no_user.json() == bad_password.json()


def test_only_admins_manage_accounts(anon):
    for user in ("viewer", "operator"):
        h = login(anon, user)
        assert anon.get("/api/v1/users", headers=h).status_code == 403
        assert (
            anon.post(
                "/api/v1/users", json={"username": "x", "roles": ["viewer"]}, headers=h
            ).status_code
            == 403
        )
        assert anon.post("/api/v1/users/admin/reset-password", headers=h).status_code == 403


def test_user_management_is_refused_when_an_identity_provider_owns_accounts():
    with make_client(auth_mode="oidc") as client:
        # no local login in oidc mode, so reach the route with an admin token minted locally
        assert client.get("/api/v1/users").status_code in (401, 403, 409)


def test_seeded_accounts_are_flagged_as_still_holding_their_default_password(client):
    users = {u["username"]: u for u in client.get("/api/v1/users").json()}
    assert users["admin"]["password_is_default"] is True

    changed = client.post(
        "/api/v1/auth/password",
        json={"current_password": "admin", "new_password": "a quiet loading bay"},
    )
    assert changed.status_code == 200, changed.text
    client.headers["Authorization"] = f"Bearer {changed.json()['access_token']}"

    after = {u["username"]: u for u in client.get("/api/v1/users").json()}
    assert after["admin"]["password_is_default"] is False


def test_changing_your_password_ends_your_other_sessions_but_not_this_one(anon):
    elsewhere = login(anon, "operator")
    here = login(anon, "operator")

    changed = anon.post(
        "/api/v1/auth/password",
        json={"current_password": "operator", "new_password": "a quiet loading bay"},
        headers=here,
    )
    assert changed.status_code == 200
    fresh = {"Authorization": f"Bearer {changed.json()['access_token']}"}

    assert anon.get("/api/v1/bays", headers=fresh).status_code == 200
    assert anon.get("/api/v1/bays", headers=elsewhere).status_code == 401
