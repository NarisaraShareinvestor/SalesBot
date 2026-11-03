"""Shared helpers: expiry computation + Customer serialization (used by chat, CRUD, notifications)."""
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from models import Customer

# สัญญาที่หมดภายในกี่วัน ถือว่า "ใกล้หมด"
EXPIRING_THRESHOLD_DAYS = int(os.getenv("EXPIRING_THRESHOLD_DAYS", "60"))

BKK_TZ = timezone(timedelta(hours=7))


def now_bkk() -> datetime:
    """เวลาไทยปัจจุบัน (naive) — container/VPS เป็น UTC ห้ามใช้ datetime.now() ตรงๆ
    เพราะ due_date/วันหมดอายุทั้งระบบตีความเป็นเวลาไทย"""
    return datetime.now(BKK_TZ).replace(tzinfo=None)


def expiry_state(expiry_date: Optional[datetime], threshold_days: int = EXPIRING_THRESHOLD_DAYS) -> Dict:
    """คำนวณสถานะจากวันหมดอายุเทียบวันนี้ (ไม่เชื่อคอลัมน์ status ที่อาจ stale)."""
    if not expiry_date:
        return {"state": "unknown", "days_to_expiry": None}
    days = (expiry_date.date() - now_bkk().date()).days
    if days < 0:
        state = "expired"
    elif days <= threshold_days:
        state = "expiring_soon"
    else:
        state = "active"
    return {"state": state, "days_to_expiry": days}


# ── Lifecycle: the ONE definition of a customer's status, used by dashboard, data table,
#    notifications, radar and every chat tool — so the numbers match everywhere.
STAGES = {
    "active": "ใช้งานอยู่",            # expiry more than EXPIRING_THRESHOLD_DAYS away
    "expiring_soon": "ใกล้หมด",        # 0..EXPIRING_THRESHOLD_DAYS days left
    "expired": "หมดอายุ ยังไม่ต่อ",     # expiry passed and not cancelled
    "cancelled": "ยกเลิกแล้ว",          # contract_status = Cancelled (any date)
    "pending": "กำลังดำเนินการ",        # no expiry date yet, status On Process / Payment Pending
    "unknown": "ไม่มีวันหมดอายุ",       # no expiry date, nothing else to go on
}
PIPELINE_STAGES = ("active", "expiring_soon", "expired")  # still a customer we sell to


def lifecycle(c: Customer) -> Dict:
    """stage + days left. `status_stale` = the stored status still says Active but the date has passed."""
    status = (c.contract_status or "").strip().lower()
    es = expiry_state(c.expiry_date)
    if status == "cancelled":
        stage = "cancelled"
    elif es["state"] != "unknown":
        stage = es["state"]
    elif status in ("on process", "payment pending"):
        stage = "pending"
    else:
        stage = "unknown"
    return {"stage": stage, "stage_label": STAGES[stage], "days_to_expiry": es["days_to_expiry"],
            "status_stale": stage == "expired" and status == "active"}


def customer_summary(c: Customer) -> Dict:
    """ข้อมูลย่อสำหรับตาราง / รายการ chat."""
    lc = lifecycle(c)
    return {
        "account": c.account,
        "company_name": c.company_name_en or c.company_name_th,
        "ae_ir": c.ae_ir,
        "product": c.contract_type,
        "expiry_date": c.expiry_date.strftime("%Y-%m-%d") if c.expiry_date else None,
        "status": c.contract_status,
        "value": c.latest_value,
        "stage": lc["stage"],
        "stage_label": lc["stage_label"],
        "status_stale": lc["status_stale"],
        "expiry_state": lc["stage"],
        "days_to_expiry": lc["days_to_expiry"],
    }


def dashboard_data(db) -> Dict:
    """ภาพรวมทั้งทีม + แยกตาม AE — นับด้วย lifecycle() ตัวเดียวกับแชท/ตาราง/แจ้งเตือน."""
    totals = {"customers": 0, "total_value": 0.0, "unassigned": 0, "status_stale": 0,
              **{s: 0 for s in STAGES}}
    by_ae: Dict[str, Dict] = {}

    for c in db.query(Customer).all():
        lc = lifecycle(c)
        stage = lc["stage"]
        value = c.latest_value or 0
        totals["customers"] += 1
        totals[stage] += 1
        if stage in PIPELINE_STAGES:
            totals["total_value"] += value
            if not c.ae_ir:
                totals["unassigned"] += 1
        if lc["status_stale"]:
            totals["status_stale"] += 1

        ae = c.ae_ir or "ไม่ระบุ"
        a = by_ae.setdefault(ae, {"ae": ae, "customers": 0, "total_value": 0.0, **{s: 0 for s in STAGES}})
        a["customers"] += 1
        a[stage] += 1
        if stage in PIPELINE_STAGES:
            a["total_value"] += value

    return {
        "stages": STAGES,
        "threshold_days": EXPIRING_THRESHOLD_DAYS,
        "totals": totals,
        "by_ae": sorted(by_ae.values(), key=lambda x: x["customers"], reverse=True),
    }


def customer_full(c: Customer) -> Dict:
    """ข้อมูลเต็มของลูกค้า 1 ราย."""
    d = customer_summary(c)
    d.update({
        # raw column names too — the edit form binds to these
        "contract_type": c.contract_type,
        "contract_status": c.contract_status,
        "latest_value": c.latest_value,
        "company_name_th": c.company_name_th,
        "company_name_en": c.company_name_en,
        "period_months": c.period_months,
        "payment_cycle": c.payment_cycle,
        "monthly_payment": c.monthly_payment,
        "effective_date": c.effective_date.strftime("%Y-%m-%d") if c.effective_date else None,
        "contract_status_text": c.contract_status_text,
        "market": c.market,
        "industry": c.industry,
        "sector": c.sector,
        "url": c.url,
        "address_th": c.address_th,
        "address_en": c.address_en,
        "zip_code": c.zip_code,
        "grade": c.grade,
        "ir_team": c.ir_team,
        "contact_email": c.contact_email,
        "cc_emails": c.cc_emails,
        "extra": c.extra,
    })
    # แนวโน้มมูลค่าสัญญารายปี (จาก extra) — import ในฟังก์ชันเลี่ยง circular import
    try:
        from radar import value_trend
        d["value_trend"] = value_trend(c.extra)
    except Exception:
        pass
    return d
