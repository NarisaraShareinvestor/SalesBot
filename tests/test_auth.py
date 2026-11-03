"""Server-side login: no cookie → 401 on every data API; the one team account → cookie.

Run against a COPY of the DB:  DATABASE_URL=sqlite:////tmp/copy.db python -m pytest tests -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ["LOGIN_USER"] = "admin"
os.environ["LOGIN_PASSWORD"] = "test-pw"
os.environ["SESSION_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import main  # noqa: E402

DATA_ROUTES = ["/api/dashboard", "/api/customers", "/api/customers/expiring", "/api/notifications",
               "/api/followups", "/api/aes", "/api/users", "/api/radar/retain", "/api/export/customers",
               "/api/customers/PLE", "/api/briefing"]


def _login(c, username="admin", password="test-pw"):
    return c.post("/api/auth/login", json={"username": username, "password": password})


def test_data_routes_need_a_session():
    c = TestClient(main.app)
    for path in DATA_ROUTES:
        assert c.get(path).status_code == 401, path
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert c.get("/").status_code == 200          # the page itself loads (shows the login screen)
    assert c.get("/static/glass.css").status_code == 200


def test_removed_routes_are_gone():
    c = TestClient(main.app)
    assert _login(c).status_code == 200
    for path in ("/api/ae-emails", "/auth/microsoft/login", "/api/auth/config"):
        assert c.get(path).status_code in (404, 405), path
    assert c.post("/api/notifications/send-email", json={}).status_code in (404, 405)


def test_login_rules():
    c = TestClient(main.app)
    auth._attempts.clear()
    assert _login(c, password="wrong").status_code == 401
    assert _login(c, username="someone").status_code == 401
    r = _login(c, username=" Admin ")             # username is trimmed + case-insensitive
    assert r.status_code == 200 and auth.COOKIE in r.cookies
    assert c.get("/api/auth/me").json()["name"] == "Admin"
    for path in DATA_ROUTES[:-1]:  # briefing may call the LLM — skip it here
        assert c.get(path).status_code == 200, path
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/dashboard").status_code == 401


def test_forged_or_tampered_cookie_is_rejected():
    good = auth.issue("admin")
    body, _ = good.rsplit(".", 1)
    for bad in ("x", body + ".deadbeef", good[:-2] + "00"):
        c = TestClient(main.app, cookies={auth.COOKIE: bad})
        assert c.get("/api/dashboard").status_code == 401
    assert TestClient(main.app, cookies={auth.COOKIE: good}).get("/api/dashboard").status_code == 200


def test_password_attempts_are_throttled():
    c = TestClient(main.app)
    auth._attempts.clear()
    codes = [_login(c, password="nope").status_code for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[-1] == 429
    auth._attempts.clear()
