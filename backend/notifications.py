"""Notifications — contracts that expire soon (shown in the app's bell + Notifications page)."""
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from models import Customer
from services import EXPIRING_THRESHOLD_DAYS, customer_summary, expiry_state, lifecycle


def compute_notifications(db: Session, ae: Optional[str] = None,
                          threshold_days: int = EXPIRING_THRESHOLD_DAYS) -> List[Dict]:
    """สัญญาที่หมดภายใน threshold_days วัน (ยังไม่เลย, ไม่นับที่ยกเลิก). เรียงด่วนสุดก่อน."""
    q = db.query(Customer).filter(Customer.expiry_date.isnot(None))
    if ae:
        q = q.filter(Customer.ae_ir == ae)
    items = []
    for c in q.all():
        if lifecycle(c)["stage"] == "cancelled":
            continue
        days = expiry_state(c.expiry_date)["days_to_expiry"]
        if 0 <= days <= threshold_days:
            items.append(customer_summary(c))
    items.sort(key=lambda r: (r["days_to_expiry"] if r["days_to_expiry"] is not None else 9999))
    return items


def notification_summary(db: Session, ae: Optional[str] = None) -> Dict:
    items = compute_notifications(db, ae)
    expired = [i for i in items if i["expiry_state"] == "expired"]
    soon = [i for i in items if i["expiry_state"] == "expiring_soon"]
    return {
        "count": len(items),
        "expired_count": len(expired),
        "expiring_soon_count": len(soon),
        "threshold_days": EXPIRING_THRESHOLD_DAYS,
        "items": items,
    }
