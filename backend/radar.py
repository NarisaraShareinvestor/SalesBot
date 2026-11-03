"""Sales Opportunity Radar — derive actionable lists from existing data.

3 ช่องทางรายได้ (คำนวณจากข้อมูลที่มีอยู่ ไม่ต้องเพิ่ม data):
  - retain  : สัญญาใกล้หมด + คะแนนเสี่ยงหลุด (มูลค่าลดลง YoY, เกรด, remark)
  - winback : ลูกค้าที่ยกเลิก/หมด เรียงตามมูลค่าเดิม
  - upsell  : ลูกค้า IR อย่างเดียว เรียงตามเกรด+มูลค่า → เสนอ WD
"""
import re
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from models import Customer
from services import EXPIRING_THRESHOLD_DAYS, lifecycle

_VALUE_YEAR_RE = re.compile(r"contract value \((\d{4})\)")
_NEG_KEYWORDS = ["ไม่ต่อ", "ยกเลิก", "ขอลด", "พิจารณา", "รอ", "ติดปัญหา", "ลดงบ", "ตัดงบ", "เงียบ"]
_GRADE_RISK = {"A": 0, "B": 8, "C": 18, "D": 26}  # เกรดต่ำ = เสี่ยงกว่า


def value_trend(extra: Optional[dict]) -> Dict:
    """ดึงประวัติมูลค่าสัญญารายปีจาก extra → series + แนวโน้ม."""
    if not extra:
        return {"series": [], "trend": None, "change_pct": None}
    pairs = []
    for k, v in extra.items():
        m = _VALUE_YEAR_RE.fullmatch(str(k).strip().lower())
        if not m:
            continue
        try:
            val = float(str(v).replace(",", ""))
        except (ValueError, TypeError):
            continue
        if val > 0:
            pairs.append((int(m.group(1)), round(val)))
    pairs.sort()
    series = [{"year": y, "value": val} for y, val in pairs]
    if len(pairs) < 2:
        return {"series": series, "trend": None, "change_pct": None}
    first, last = pairs[0][1], pairs[-1][1]
    change = (last - first) / first * 100 if first else 0
    trend = "down" if change <= -10 else ("up" if change >= 10 else "flat")
    return {"series": series, "trend": trend, "change_pct": round(change, 1)}


def _risk(c: Customer) -> Dict:
    """คะแนนเสี่ยงหลุด 0-100 + เหตุผล (โปร่งใส อธิบายได้)."""
    score, reasons = 0, []
    vt = value_trend(c.extra)
    if vt["trend"] == "down":
        score += 40
        reasons.append(f"มูลค่าลดลง {abs(vt['change_pct'])}% (จากประวัติ)")
    elif vt["trend"] == "flat":
        score += 8
    g = (c.grade or "").strip().upper()
    if g in _GRADE_RISK:
        score += _GRADE_RISK[g]
        if _GRADE_RISK[g] >= 18:
            reasons.append(f"เกรด {g}")
    remark = (c.contract_status_text or "")
    for kw in _NEG_KEYWORDS:
        if kw in remark:
            score += 20
            reasons.append(f"remark: \"{remark[:40]}\"")
            break
    return {"score": min(score, 100), "reasons": reasons, "trend": vt["trend"], "change_pct": vt["change_pct"]}


def _base(c: Customer, lc: Dict) -> Dict:
    return {
        "account": c.account,
        "company_name": c.company_name_en or c.company_name_th,
        "ae_ir": c.ae_ir,
        "product": c.contract_type,
        "grade": c.grade,
        "value": c.latest_value,
        "expiry_date": c.expiry_date.strftime("%Y-%m-%d") if c.expiry_date else None,
        "stage": lc["stage"],
        "stage_label": lc["stage_label"],
        "expiry_state": lc["stage"],
        "days_to_expiry": lc["days_to_expiry"],
    }


def _level(score: int) -> str:
    return "high" if score >= 50 else ("mid" if score >= 25 else "low")


def retain(db: Session, ae: Optional[str] = None, within_days: int = EXPIRING_THRESHOLD_DAYS,
           limit: int = 60) -> Dict:
    """สัญญาใกล้หมด (ไม่นับ Cancelled) เรียงตามความเสี่ยง × มูลค่า."""
    q = db.query(Customer)
    if ae:
        q = q.filter(Customer.ae_ir == ae)
    rows = []
    for c in q.all():
        lc = lifecycle(c)
        if lc["stage"] != "expiring_soon":
            continue
        r = _risk(c)
        d = _base(c, lc)
        d.update({"risk_score": r["score"], "risk_level": _level(r["score"]),
                  "reasons": r["reasons"], "trend": r["trend"], "change_pct": r["change_pct"]})
        rows.append(d)
    # priority: เสี่ยงสูง + มูลค่าสูง ขึ้นก่อน
    rows.sort(key=lambda x: (x["risk_score"], x["value"] or 0), reverse=True)
    return {"items": rows[:limit], "total": len(rows)}


def renewal_outlook(db: Session, horizon_days: int = 90, lapsed_days: int = 90, limit: int = 8) -> Dict:
    """For the dashboard: value due in the next 30/60/90 days, who to call first, and what lapsed
    recently (still worth chasing). Cancelled contracts are never counted."""
    buckets = [{"within": d, "count": 0, "value": 0.0} for d in (30, 60, 90) if d <= horizon_days]
    upcoming, lapsed = [], []
    lapsed_value = 0.0
    for c in db.query(Customer).all():
        lc = lifecycle(c)
        days = lc["days_to_expiry"]
        if lc["stage"] == "cancelled" or days is None:
            continue
        if 0 <= days <= horizon_days:
            for b in buckets:
                if days <= b["within"]:
                    b["count"] += 1
                    b["value"] += c.latest_value or 0
                    break
            r = _risk(c)
            d = _base(c, lc)
            d.update({"risk_level": _level(r["score"]), "reasons": r["reasons"]})
            upcoming.append(d)
        elif -lapsed_days <= days < 0:
            lapsed.append(_base(c, lc) | {"status_stale": lc["status_stale"]})
            lapsed_value += c.latest_value or 0
    for b in buckets:
        b["value"] = round(b["value"])
    upcoming.sort(key=lambda x: x["days_to_expiry"])
    lapsed.sort(key=lambda x: (x["value"] or 0), reverse=True)
    return {"buckets": buckets, "upcoming": upcoming[:limit], "upcoming_total": len(upcoming),
            "lapsed": lapsed[:limit], "lapsed_total": len(lapsed), "lapsed_value": round(lapsed_value),
            "lapsed_days": lapsed_days}


def winback(db: Session, ae: Optional[str] = None, limit: int = 60) -> Dict:
    """ลูกค้าที่ยกเลิก/หมดสัญญา (ตามวันจริง ไม่ใช่คอลัมน์ status) เรียงตามมูลค่าเดิม."""
    q = db.query(Customer)
    if ae:
        q = q.filter(Customer.ae_ir == ae)
    rows = []
    total_value = 0.0
    for c in q.all():
        lc = lifecycle(c)
        if lc["stage"] not in ("cancelled", "expired"):
            continue
        vt = value_trend(c.extra)
        d = _base(c, lc)
        d.update({"lost_reason": lc["stage_label"], "status_stale": lc["status_stale"], "trend": vt["trend"],
                  "change_pct": vt["change_pct"], "remark": c.contract_status_text})
        rows.append(d)
        total_value += c.latest_value or 0
    rows.sort(key=lambda x: (x["value"] or 0), reverse=True)
    return {"items": rows[:limit], "total": len(rows), "total_value": round(total_value)}


def upsell(db: Session, ae: Optional[str] = None, limit: int = 60) -> Dict:
    """ลูกค้า IR อย่างเดียว (ยังไม่มี WD) เรียงตามเกรด+มูลค่า → เสนอ WD."""
    _grade_boost = {"A": 30, "B": 20, "C": 10, "D": 5}
    q = db.query(Customer).filter(Customer.contract_type == "IR")
    if ae:
        q = q.filter(Customer.ae_ir == ae)
    rows = []
    for c in q.all():
        lc = lifecycle(c)
        if lc["stage"] not in ("active", "expiring_soon"):  # หมด/ยกเลิกแล้วไปอยู่ win-back
            continue
        vt = value_trend(c.extra)
        g = (c.grade or "").strip().upper()
        score = _grade_boost.get(g, 0) + min((c.latest_value or 0) / 10000, 40)
        d = _base(c, lc)
        d.update({"upsell_score": round(score), "trend": vt["trend"], "change_pct": vt["change_pct"]})
        rows.append(d)
    rows.sort(key=lambda x: x["upsell_score"], reverse=True)
    return {"items": rows[:limit], "total": len(rows)}
