"""Server-side login: no cookie → 401 on every data API; team password + company email → cookie.

Run against a COPY of the DB:  DATABASE_URL=sqlite:////tmp/copy.db python -m pytest tests -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ["TEAM_PASSWORD"] = "test-team-pw"
os.environ["SESSION_SECRET"] = "test-secret"
for k in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402
import main  # noqa: E402

DATA_ROUTES = ["/api/dashboard", "/api/customers", "/api/customers/expiring", "/api/notifications",
               "/api/followups", "/api/aes", "/api/users", "/api/radar/retain", "/api/export/customers",
               "/api/customers/PLE", "/api/briefing"]


def _login(c, **kw):
    body = {"email": "a.b@shareinvestor.com", "name": "Test User", "password": "test-team-pw"} | kw
    return c.post("/api/auth/login", json=body)


def test_data_routes_need_a_session():
    c = TestClient(main.app)
    for path in DATA_ROUTES:
        assert c.get(path).status_code == 401, path
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert c.get("/").status_code == 200          # the page itself loads (shows the login screen)
    assert c.get("/static/glass.css").status_code == 200
    assert c.get("/api/auth/config").json() == {"teams": False}


def test_login_rules():
    c = TestClient(main.app)
    assert _login(c, password="wrong").status_code == 401
    assert _login(c, email="someone@gmail.com").status_code == 400
    assert _login(c, name=" ").status_code == 400
    r = _login(c)
    assert r.status_code == 200 and auth.COOKIE in r.cookies
    assert c.get("/api/auth/me").json()["email"] == "a.b@shareinvestor.com"
    for path in DATA_ROUTES[:-1]:  # briefing may call the LLM — skip it here
        assert c.get(path).status_code == 200, path
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get("/api/dashboard").status_code == 401


def test_forged_or_tampered_cookie_is_rejected():
    good = auth.issue("a.b@shareinvestor.com", "Test User")
    body, sig = good.rsplit(".", 1)
    for bad in ("x", body + ".deadbeef", auth.issue("a.b@shareinvestor.com", "x")[:-2] + "00"):
        c = TestClient(main.app, cookies={auth.COOKIE: bad})
        assert c.get("/api/dashboard").status_code == 401
    assert TestClient(main.app, cookies={auth.COOKIE: good}).get("/api/dashboard").status_code == 200


def test_password_attempts_are_throttled():
    c = TestClient(main.app)
    auth._attempts.clear()
    codes = [_login(c, password="nope").status_code for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[-1] == 429
    auth._attempts.clear()
