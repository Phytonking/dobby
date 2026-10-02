"""Endpoint tests against the FastAPI app with the DB dependency overridden."""

import bcrypt

from dashboard import auth as auth_mod
from dashboard.auth import COOKIE_NAME, get_current_user, make_token

from .conftest import FakeDB, make_db_session, make_user


# ---------------------------------------------------------------------------
# /health and auth gating
# ---------------------------------------------------------------------------


def test_health_needs_no_auth(client):
    c = client(FakeDB())
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_me_without_cookie_is_401(client):
    c = client(FakeDB())
    assert c.get("/me").status_code == 401


def test_me_with_invalid_cookie_is_401(client):
    c = client(FakeDB())
    c.cookies.set(COOKIE_NAME, "forged-token")
    assert c.get("/me").status_code == 401


def test_me_with_expired_session_is_401(client):
    user = make_user()
    token = make_token(str(user.id))
    # get_current_user filters expired sessions in SQL; an expired session row
    # means the query returns nothing.
    c = client(FakeDB(results=[None]))
    c.cookies.set(COOKIE_NAME, token)
    assert c.get("/me").status_code == 401


def test_me_with_valid_session_returns_user(client):
    user = make_user(role="admin")
    token = make_token(str(user.id))
    session = make_db_session(user, token)
    c = client(FakeDB(results=[session, user]))
    c.cookies.set(COOKIE_NAME, token)

    r = c.get("/me")
    assert r.status_code == 200
    body = r.json()
    assert body["uw_email"] == "student@uw.edu"
    assert body["role"] == "admin"


# ---------------------------------------------------------------------------
# Admin role enforcement
# ---------------------------------------------------------------------------


def test_admin_route_forbidden_for_student(app, client):
    student = make_user(role="student")

    async def fake_current_user():
        return student

    app.dependency_overrides[get_current_user] = fake_current_user
    c = client(FakeDB())
    assert c.get("/admin/users").status_code == 403


def test_admin_route_allowed_for_admin(app, client):
    admin = make_user(role="admin")

    async def fake_current_user():
        return admin

    app.dependency_overrides[get_current_user] = fake_current_user
    # list_users: first execute → count, second → user rows
    c = client(FakeDB(results=[1, [admin]]))
    r = c.get("/admin/users")
    assert r.status_code == 200
    assert r.json()["total"] == 1


# ---------------------------------------------------------------------------
# Local login
# ---------------------------------------------------------------------------


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()


def test_local_login_unknown_user_is_401(client):
    c = client(FakeDB(results=[None]))
    r = c.post("/auth/local", json={"username": "ghost", "password": "pw"})
    assert r.status_code == 401


def test_local_login_wrong_password_is_401(client):
    user = make_user(password_hash=_hash("right"))
    c = client(FakeDB(results=[user]))
    r = c.post("/auth/local", json={"username": "student@uw.edu", "password": "wrong"})
    assert r.status_code == 401


def test_local_login_success_sets_session_cookie(client):
    user = make_user(password_hash=_hash("right"))
    db = FakeDB(results=[user])
    c = client(db)
    r = c.post("/auth/local", json={"username": "student@uw.edu", "password": "right"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert COOKIE_NAME in r.cookies
    # create_session persisted a session row
    assert db.committed
    assert len(db.added) == 1
    assert auth_mod.verify_token(db.added[0].token) == str(user.id)


def test_local_login_user_without_password_hash_is_401(client):
    user = make_user(password_hash=None)
    c = client(FakeDB(results=[user]))
    r = c.post("/auth/local", json={"username": "student@uw.edu", "password": "pw"})
    assert r.status_code == 401
