"""One-click lifecycle actions (renew / lost) and chat history.

Run against a COPY of the DB:  DATABASE_URL=sqlite:////tmp/copy.db python -m pytest tests -q
"""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("LOGIN_PASSWORD", "test-pw")
os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from database import SessionLocal  # noqa: E402
from memory_manager import CustomerMemoryManager  # noqa: E402
from services import dashboard_data, lifecycle  # noqa: E402
from models import Customer  # noqa: E402


def _client():
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": os.environ["LOGIN_PASSWORD"]}).status_code == 200
    return c


def _pick(stale: bool):
    db = SessionLocal()
    for cust in db.query(Customer).all():
        lc = lifecycle(cust)
        if lc["status_stale"] == stale and (stale or lc["stage"] == "expiring_soon"):
            return cust.account
    raise AssertionError("no suitable customer in the DB copy")


def test_add_months_handles_month_ends():
    assert main._add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert main._add_months(datetime(2028, 1, 31), 1) == datetime(2028, 2, 29)
    assert main._add_months(datetime(2026, 11, 15), 12) == datetime(2027, 11, 15)


def test_renew_clears_a_stale_status_and_moves_the_dashboard():
    c = _client()
    acct = _pick(stale=True)
    before = dashboard_data(SessionLocal())["totals"]
    r = c.post(f"/api/customers/{acct}/renew", json={"months": 12})
    assert r.status_code == 200
    d = r.json()
    assert d["contract_status"] == "Active" and d["stage"] in ("active", "expiring_soon") and not d["status_stale"]
    after = dashboard_data(SessionLocal())["totals"]
    assert after["status_stale"] == before["status_stale"] - 1
    assert after["expired"] == before["expired"] - 1
    assert any("ต่อสัญญา 12 เดือน" in f for f in CustomerMemoryManager.list_facts(SessionLocal(), acct))


def test_lost_needs_a_reason_and_goes_to_winback():
    c = _client()
    acct = _pick(stale=False)
    assert c.post(f"/api/customers/{acct}/lost", json={"reason": " "}).status_code == 400
    r = c.post(f"/api/customers/{acct}/lost", json={"reason": "ลดงบ IR ปีนี้"})
    assert r.status_code == 200 and r.json()["stage"] == "cancelled"
    winback = c.get("/api/radar/winback").json()
    assert any(i["account"] == acct for i in winback["items"]) or winback["total"] > len(winback["items"])
    assert any("ลดงบ IR ปีนี้" in f for f in CustomerMemoryManager.list_facts(SessionLocal(), acct))


def test_renew_validates_months_and_account():
    c = _client()
    assert c.post("/api/customers/NOPE_X/renew", json={}).status_code == 404
    assert c.post(f"/api/customers/{_pick(stale=True)}/renew", json={"months": 99}).status_code == 400


def test_chat_history_is_per_session_and_needs_login():
    assert TestClient(main.app).get("/api/chat/history").status_code == 401
    assert isinstance(_client().get("/api/chat/history").json(), list)
