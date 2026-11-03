"""Every place that counts customers must agree: dashboard, chat tools, data table, radar, notifications.

Run against a COPY of the DB:  DATABASE_URL=sqlite:////tmp/copy.db python -m pytest tests -q
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
assert os.getenv("DATABASE_URL"), "point DATABASE_URL at a copy of salesbot.db"

import main  # noqa: E402  (runs the startup migration on the copy)
import radar  # noqa: E402
from chat_tools import run_tool  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import Customer  # noqa: E402
from notifications import compute_notifications  # noqa: E402
from services import STAGES, dashboard_data, lifecycle  # noqa: E402


def _db():
    return SessionLocal()


def test_stage_counts_match_everywhere():
    db = _db()
    totals = dashboard_data(db)["totals"]
    agg = {r["group"]: r["count"] for r in run_tool("aggregate", {"group_by": "stage", "metric": "count"}, db)}
    for key, label in STAGES.items():
        listed = run_tool("list_customers", {"stage": key, "limit": 1}, db)["total"]
        table = main.list_customers(db=db, search=None, ae=None, stage=key, page=1, page_size=1)["total"]
        assert totals[key] == agg.get(label, 0) == listed == table, key
    assert sum(totals[k] for k in STAGES) == totals["customers"] == db.query(Customer).count()


def test_per_ae_counts_match_chat():
    db = _db()
    for row in dashboard_data(db)["by_ae"]:
        if row["ae"] == "ไม่ระบุ":
            continue
        got = run_tool("aggregate", {"group_by": "stage", "metric": "count", "filter_ae": row["ae"]}, db)
        # filter_ae is a substring match in chat; only compare AEs whose name isn't inside another's
        others = [r["ae"] for r in dashboard_data(db)["by_ae"] if r["ae"] != row["ae"] and row["ae"].lower() in r["ae"].lower()]
        if others:
            continue
        assert sum(r["count"] for r in got) == row["customers"], row["ae"]


def test_cancelled_is_normalised_and_never_counted_as_pipeline():
    db = _db()
    raw = {c.contract_status for c in db.query(Customer).all() if (c.contract_status or "").lower() == "cancelled"}
    assert raw <= {"Cancelled"}
    for c in db.query(Customer).all():
        if (c.contract_status or "").lower() == "cancelled":
            assert lifecycle(c)["stage"] == "cancelled"


def test_radar_and_notifications_use_same_stages():
    db = _db()
    totals = dashboard_data(db)["totals"]
    assert radar.retain(db)["total"] == totals["expiring_soon"]
    assert len(compute_notifications(db)) == totals["expiring_soon"]
    assert radar.winback(db)["total"] == totals["expired"] + totals["cancelled"]
    for item in radar.upsell(db)["items"]:
        assert item["stage"] in ("active", "expiring_soon")


def test_stale_flag_means_status_active_but_date_passed():
    db = _db()
    stale = run_tool("list_customers", {"status_stale_only": True, "limit": 100}, db)
    assert stale["total"] == dashboard_data(db)["totals"]["status_stale"]
    for i in stale["items"]:
        assert i["stage"] == "expired" and (i["status"] or "").lower() == "active"
