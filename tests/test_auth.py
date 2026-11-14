"""Sign-in, sessions and roles (real Postgres)."""

import hashlib

import psycopg
import pytest
from fastapi.testclient import TestClient

from api import create_app
from api.auth import COOKIE, Throttle
from db import Repo
from db.passwords import hash_password, verify_password


def test_password_hashing():
    h = hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct horse" not in h
    assert verify_password("correct horse battery", h)
    assert not verify_password("correct horse batterY", h)
    assert hash_password("correct horse battery") != h  # salted
    assert not verify_password("anything at all", "not-a-hash")
    with pytest.raises(ValueError):
        hash_password("short")


def test_throttle_counts_failures_in_a_window():
    t = Throttle(limit=2, window_s=60)
    assert t.retry_after("k") == 0
    t.fail("k")
    t.fail("k")
    assert 0 < t.retry_after("k") <= 61 and t.retry_after("other") == 0
    t.clear("k")
    assert t.retry_after("k") == 0


@pytest.fixture(scope="module")
def app(pg_url, make_user):
    creds = {"auth-op": make_user("auth-op"), "auth-view": make_user("auth-view", "viewer")}
    r = Repo(pg_url, max_size=1)
    r.delete_scenario("au1")
    r.close()
    with TestClient(create_app(pg_url)) as c:
        assert c.post("/auth/login", json={"username": "auth-op", "password": creds["auth-op"]}).status_code == 200
        assert c.post("/scenarios", json={"scenario_id": "au1", "seed": 3, "n_shipments": 300}).status_code == 201
        c.post("/auth/logout")
        yield c, creds


def _login(c, creds, user):
    r = c.post("/auth/login", json={"username": user, "password": creds[user]})
    assert r.status_code == 200, r.text
    return r


def test_everything_but_public_routes_needs_a_session(app):
    c, _ = app
    c.cookies.clear()
    for path in ("/health", "/benchmark", "/openapi.json"):
        assert c.get(path).status_code == 200, path
    for path in ("/scenarios", "/scenarios/au1/detections", "/scenarios/au1/decisions", "/scenarios/au1/events",
                 "/scenarios/au1/evaluation/detections", "/auth/me"):
        assert c.get(path).status_code == 401, path
    assert c.post("/scenarios/au1/plans", json={}).status_code == 401
    c.cookies.set(COOKIE, "forged-token")
    assert c.get("/scenarios").status_code == 401


def test_login_sets_an_httponly_cookie_and_stores_only_a_hash(app, pg_url):
    c, creds = app
    c.cookies.clear()
    r = _login(c, creds, "auth-op")
    assert r.json() == {"username": "auth-op", "display_name": "Auth-Op", "role": "operator"}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    token = c.cookies[COOKIE]
    with psycopg.connect(pg_url) as conn:
        stored = [bytes(t) for (t,) in conn.execute("SELECT token_sha256 FROM sessions WHERE username = 'auth-op'")]
    assert hashlib.sha256(token.encode()).digest() in stored and token.encode() not in stored
    assert c.get("/auth/me").json()["username"] == "auth-op"


def test_wrong_passwords_and_unknown_users_look_the_same_then_get_throttled(app, make_user):
    c, creds = app
    c.cookies.clear()
    password = make_user("auth-lock", "viewer")
    a = c.post("/auth/login", json={"username": "auth-lock", "password": "wrong password!"})
    b = c.post("/auth/login", json={"username": "nobody-here", "password": "wrong password!"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    for _ in range(4):
        c.post("/auth/login", json={"username": "auth-lock", "password": "wrong password!"})
    r = c.post("/auth/login", json={"username": "auth-lock", "password": password})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0  # even the right password, for now
    assert c.post("/auth/login", json={"username": "auth-op", "password": creds["auth-op"]}).status_code == 200


def test_decisions_record_the_signed_in_user_not_what_the_body_claims(app):
    c, creds = app
    c.cookies.clear()
    _login(c, creds, "auth-op")
    sid = next(d["shipment_id"] for d in c.get("/scenarios/au1/detections").json()["items"] if not d["decision"])
    d = c.post("/scenarios/au1/decisions", json={"shipment_id": sid, "action": "DISMISS", "decided_by": "someone-else"})
    assert d.status_code == 201 and d.json()["decided_by"] == "auth-op"
    c.post("/scenarios/au1/decisions", json={"shipment_id": sid, "action": "REVOKE"})


def test_viewers_can_read_but_not_change_anything(app):
    c, creds = app
    c.cookies.clear()
    _login(c, creds, "auth-view")
    assert c.get("/scenarios/au1/detections").status_code == 200
    sid = c.get("/scenarios/au1/detections").json()["items"][0]["shipment_id"]
    for path, body in (("/scenarios/au1/decisions", {"shipment_id": sid, "action": "DISMISS"}),
                       ("/scenarios/au1/plans", {"solver": "greedy"}),
                       ("/scenarios", {"scenario_id": "au_view", "seed": 1})):
        r = c.post(path, json=body)
        assert r.status_code == 403 and "read-only" in r.json()["detail"], path


def test_changes_from_a_foreign_origin_are_refused(app):
    c, creds = app
    c.cookies.clear()
    _login(c, creds, "auth-op")
    r = c.post("/scenarios/au1/plans", json={"solver": "none"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and "cross-site" in r.json()["detail"]
    assert c.post("/scenarios/au1/plans", json={"solver": "none"},
                  headers={"Origin": "http://localhost:5173"}).status_code == 201  # the dev server


def test_logout_password_change_and_disabling_end_sessions(app, pg_url, make_user):
    c, _ = app
    c.cookies.clear()
    password = make_user("auth-tmp")
    c.post("/auth/login", json={"username": "auth-tmp", "password": password})
    token = c.cookies[COOKIE]
    assert c.post("/auth/logout").status_code == 204
    c.cookies.set(COOKIE, token)  # replaying the old cookie doesn't work
    assert c.get("/auth/me").status_code == 401

    r = Repo(pg_url, max_size=1)
    try:
        c.cookies.clear()
        c.post("/auth/login", json={"username": "auth-tmp", "password": password})
        r.set_password("auth-tmp", "a brand new password")
        assert c.get("/auth/me").status_code == 401
        c.post("/auth/login", json={"username": "auth-tmp", "password": "a brand new password"})
        r.set_user("auth-tmp", disabled=True)
        assert c.get("/auth/me").status_code == 401
        again = c.post("/auth/login", json={"username": "auth-tmp", "password": "a brand new password"})
        assert again.status_code == 401
        with psycopg.connect(pg_url) as conn:  # expired sessions are rejected too
            r.set_user("auth-tmp", disabled=False)
            c.post("/auth/login", json={"username": "auth-tmp", "password": "a brand new password"})
            conn.execute("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE username = 'auth-tmp'")
            conn.commit()
        assert c.get("/auth/me").status_code == 401
    finally:
        r.remove_user("auth-tmp")
        r.close()
