"""Shared helpers: expiry computation + Customer serialization (used by chat, CRUD, notifications)."""
import os
from datetime import datetime
from typing import Dict, Optional

from models import Customer

# สัญญาที่หมดภายในกี่วัน ถือว่า "ใกล้หมด"
EXPIRING_THRESHOLD_DAYS = int(os.getenv("EXPIRING_THRESHOLD_DAYS", "60"))


def expiry_state(expiry_date: Optional[datetime], threshold_days: int = EXPIRING_THRESHOLD_DAYS) -> Dict:
    """คำนวณสถานะจากวันหมดอายุเทียบวันนี้ (ไม่เชื่อคอลัมน์ status ที่อาจ stale)."""
    if not expiry_date:
        return {"state": "unknown", "days_to_expiry": None}
    days = (expiry_date.date() - datetime.now().date()).days
    if days < 0:
        state = "expired"
    elif days <= threshold_days:
        state = "expiring_soon"
    else:
        state = "active"
    return {"state": state, "days_to_expiry": days}


def customer_summary(c: Customer) -> Dict:
    """ข้อมูลย่อสำหรับตาราง / รายการ chat."""
    es = expiry_state(c.expiry_date)
    return {
        "account": c.account,
        "company_name": c.company_name_en or c.company_name_th,
        "ae_ir": c.ae_ir,
        "product": c.contract_type,
        "expiry_date": c.expiry_date.strftime("%Y-%m-%d") if c.expiry_date else None,
        "status": c.contract_status,
        "value": c.latest_value,
        "expiry_state": es["state"],
        "days_to_expiry": es["days_to_expiry"],
    }


def dashboard_data(db) -> Dict:
    """ภาพรวมทั้งทีม + แยกตาม AE (ใช้สำหรับ Dashboard / มุมมอง Lead)."""
    customers = db.query(Customer).all()
    totals = {"customers": 0, "total_value": 0.0, "active": 0,
              "expiring_soon": 0, "expired": 0, "unassigned": 0}
    by_ae: Dict[str, Dict] = {}
    by_status: Dict[str, int] = {}

    for c in customers:
        totals["customers"] += 1
        totals["total_value"] += c.latest_value or 0
        if not c.ae_ir:
            totals["unassigned"] += 1
        st = (c.contract_status or "ไม่ระบุ").strip().title()
        by_status[st] = by_status.get(st, 0) + 1

        es = expiry_state(c.expiry_date)["state"]
        dead = (c.contract_status or "").strip().lower() == "cancelled"
        if es == "active":
            totals["active"] += 1
        elif es == "expiring_soon" and not dead:
            totals["expiring_soon"] += 1
        elif es == "expired" and not dead:
            totals["expired"] += 1

        ae = c.ae_ir or "ไม่ระบุ"
        a = by_ae.setdefault(ae, {"ae": ae, "customers": 0, "active": 0,
                                  "expiring_soon": 0, "expired": 0, "total_value": 0.0})
        a["customers"] += 1
        a["total_value"] += c.latest_value or 0
        if es == "active":
            a["active"] += 1
        elif es == "expiring_soon" and not dead:
            a["expiring_soon"] += 1
        elif es == "expired" and not dead:
            a["expired"] += 1

    return {
        "totals": totals,
        "by_ae": sorted(by_ae.values(), key=lambda x: x["customers"], reverse=True),
        "by_status": [{"status": k, "count": v} for k, v in sorted(by_status.items(), key=lambda x: -x[1])],
    }


def customer_full(c: Customer) -> Dict:
    """ข้อมูลเต็มของลูกค้า 1 ราย."""
    d = customer_summary(c)
    d.update({
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
