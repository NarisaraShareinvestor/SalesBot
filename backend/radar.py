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
from services import EXPIRING_THRESHOLD_DAYS, expiry_state

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


def _base(c: Customer) -> Dict:
    es = expiry_state(c.expiry_date)
    return {
        "account": c.account,
        "company_name": c.company_name_en or c.company_name_th,
        "ae_ir": c.ae_ir,
        "product": c.contract_type,
        "grade": c.grade,
        "value": c.latest_value,
        "expiry_date": c.expiry_date.strftime("%Y-%m-%d") if c.expiry_date else None,
        "expiry_state": es["state"],
        "days_to_expiry": es["days_to_expiry"],
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
        es = expiry_state(c.expiry_date)
        if es["state"] != "expiring_soon":
            continue
        if (c.contract_status or "").strip().lower() == "cancelled":
            continue
        r = _risk(c)
        d = _base(c)
        d.update({"risk_score": r["score"], "risk_level": _level(r["score"]),
                  "reasons": r["reasons"], "trend": r["trend"], "change_pct": r["change_pct"]})
        rows.append(d)
    # priority: เสี่ยงสูง + มูลค่าสูง ขึ้นก่อน
    rows.sort(key=lambda x: (x["risk_score"], x["value"] or 0), reverse=True)
    return {"items": rows[:limit], "total": len(rows)}


def winback(db: Session, ae: Optional[str] = None, limit: int = 60) -> Dict:
    """ลูกค้าที่ยกเลิก/หมดสัญญา เรียงตามมูลค่าเดิม (โอกาสดึงกลับ)."""
    q = db.query(Customer)
    if ae:
        q = q.filter(Customer.ae_ir == ae)
    rows = []
    total_value = 0.0
    for c in q.all():
        st = (c.contract_status or "").strip().lower()
        if st not in ("cancelled", "expired"):
            continue
        vt = value_trend(c.extra)
        d = _base(c)
        d.update({"lost_reason": c.contract_status, "trend": vt["trend"],
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
        st = (c.contract_status or "").strip().lower()
        if st == "cancelled":  # ยกเลิกแล้วไปอยู่ win-back
            continue
        vt = value_trend(c.extra)
        g = (c.grade or "").strip().upper()
        score = _grade_boost.get(g, 0) + min((c.latest_value or 0) / 10000, 40)
        d = _base(c)
        d.update({"upsell_score": round(score), "trend": vt["trend"], "change_pct": vt["change_pct"]})
        rows.append(d)
    rows.sort(key=lambda x: x["upsell_score"], reverse=True)
    return {"items": rows[:limit], "total": len(rows)}
