"""Function-calling tools for the chat agent — deterministic queries over the DB.

This is the "ฉลาดขึ้นมากๆ" layer: the model calls these tools instead of us
injecting raw rows and hoping it counts/sums correctly.
"""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import Customer, FollowUp
from notifications import compute_notifications
from services import customer_full, customer_summary

# ── OpenAI tool schema ───────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_customers",
            "description": "ค้นหาลูกค้าจากชื่อย่อ (account), ชื่อบริษัท (ไทย/อังกฤษ), อีเมล, หรือที่อยู่ — คืนรายการย่อ (สถานะ/มูลค่า/วันหมด เท่านั้น). หากต้องการ URL, ที่อยู่, เบอร์ติดต่อ, remark ของลูกค้ารายใด ต้องตามด้วย get_customer(account) เสมอ",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "คำค้น เช่น 'ACE', 'พลังงาน', 'energy' หรืออีเมล 'chainat_b@ace-energy.co.th'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "ดึงข้อมูลครบทุก field ของลูกค้า 1 ราย จาก account code — รวม URL/เว็บไซต์, ที่อยู่ (TH/EN), Zip Code, อีเมลผู้ติดต่อ, CC, grade, ir_team, contract remark, market, industry, sector. **ใช้ทุกครั้งที่ถามเกี่ยวกับ URL/เว็บ/ที่อยู่/ผู้ติดต่อ/รายละเอียด** ของลูกค้ารายนั้น",
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "account code เช่น ACE, AAV, BCP"},
                },
                "required": ["account"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_expiring",
            "description": "รายการสัญญาที่ใกล้หมดอายุหรือหมดอายุแล้ว (คำนวณจากวันหมดอายุเทียบวันนี้). ระบุ AE เพื่อกรองเฉพาะลูกค้าของ AE คนนั้น",
            "parameters": {
                "type": "object",
                "properties": {
                    "within_days": {"type": "integer", "description": "ดูสัญญาที่หมดภายในกี่วัน (default 60)"},
                    "ae": {"type": "string", "description": "ชื่อ AE-IR เช่น Maprang, Lookwa (ไม่ระบุ = ทุกคน)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate",
            "description": "นับจำนวนหรือรวม/เฉลี่ยมูลค่าสัญญา แบ่งกลุ่มตามมิติที่เลือก. ใช้ตอบคำถามสถิติ เช่น 'ลูกค้าของ Maprang active กี่ราย', 'รวมมูลค่าสัญญาแต่ละ AE'",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {"type": "string", "enum": ["ae", "status", "industry", "market", "contract_type"]},
                    "metric": {"type": "string", "enum": ["count", "sum_value", "avg_value"]},
                    "filter_ae": {"type": "string", "description": "กรองเฉพาะ AE คนนี้ (optional)"},
                    "filter_status": {"type": "string", "description": "กรองเฉพาะสถานะ เช่น Active/Expired/Cancelled (optional)"},
                },
                "required": ["group_by", "metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_followup",
            "description": "บันทึกงานติดตาม / นัดหมาย ลงตาราง เช่น 'วันนี้ 14:00 โทรนัดต่อสัญญา ACE'. ใช้เมื่อผู้ใช้สั่งให้ 'ลงตาราง', 'บันทึกงาน', 'เตือน', 'นัด'",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "เรื่อง/รายละเอียดงาน เช่น 'โทรนัดต่อสัญญา'"},
                    "account": {"type": "string", "description": "account code ของลูกค้าที่เกี่ยวข้อง เช่น ACE (ไม่ใส่ถ้าเป็นงานทั่วไป)"},
                    "when": {"type": "string", "description": "วันเวลา รูปแบบ ISO เช่น '2026-06-11' หรือ '2026-06-11T14:00' (คำนวณ 'วันนี้' จากวันที่ปัจจุบันที่ให้ไว้)"},
                },
                "required": ["note"],
            },
        },
    },
]

_GROUP_COL = {
    "ae": Customer.ae_ir,
    "status": Customer.contract_status,
    "industry": Customer.industry,
    "market": Customer.market,
    "contract_type": Customer.contract_type,
}


# ── Executors ────────────────────────────────────────────────────────────────
def _search_customers(db: Session, query: str) -> List[Dict]:
    like = f"%{query.strip()}%"
    rows = db.query(Customer).filter(or_(
        Customer.account.ilike(like),
        Customer.company_name_en.ilike(like),
        Customer.company_name_th.ilike(like),
        Customer.contact_email.ilike(like),
        Customer.cc_emails.ilike(like),
        Customer.address_th.ilike(like),
        Customer.address_en.ilike(like),
        Customer.zip_code.ilike(like),
    )).limit(25).all()
    return [customer_summary(c) for c in rows]


def _get_customer(db: Session, account: str) -> Optional[Dict]:
    c = db.get(Customer, account.strip().upper())
    return customer_full(c) if c else None


def _list_expiring(db: Session, within_days: int = 60, ae: Optional[str] = None) -> List[Dict]:
    return compute_notifications(db, ae=ae, threshold_days=within_days)


def _aggregate(db: Session, group_by: str, metric: str,
               filter_ae: Optional[str] = None, filter_status: Optional[str] = None) -> List[Dict]:
    col = _GROUP_COL.get(group_by)
    if col is None:
        return [{"error": f"invalid group_by: {group_by}"}]
    if metric == "count":
        agg = func.count()
    elif metric == "sum_value":
        agg = func.sum(Customer.latest_value)
    elif metric == "avg_value":
        agg = func.avg(Customer.latest_value)
    else:
        return [{"error": f"invalid metric: {metric}"}]

    q = db.query(col, agg)
    if filter_ae:
        q = q.filter(Customer.ae_ir == filter_ae)
    if filter_status:
        q = q.filter(func.lower(Customer.contract_status) == filter_status.strip().lower())
    q = q.group_by(col).order_by(agg.desc())
    out = []
    for key, value in q.all():
        if metric != "count" and value is not None:
            value = round(float(value), 2)
        out.append({"group": key or "ไม่ระบุ", metric: value})
    return out


def _add_followup(db: Session, note: str, account: Optional[str] = None, when: Optional[str] = None,
                  created_by: Optional[str] = None, creator_email: Optional[str] = None) -> Dict:
    acct = account.strip().upper() if account else None
    if acct and not db.get(Customer, acct):
        return {"error": f"ไม่พบลูกค้า {acct}"}
    due, has_time = None, False
    if when:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                due = datetime.strptime(when.strip()[:19], fmt)
                has_time = "T" in when or ":" in when
                break
            except ValueError:
                continue
    f = FollowUp(account=acct, note=note, due_date=due, created_by=created_by, source="chat")
    db.add(f)
    db.commit()
    db.refresh(f)
    return {"ok": True, "id": f.id, "account": acct, "note": note,
            "when": due.strftime("%Y-%m-%d %H:%M") if (due and has_time) else (due.strftime("%Y-%m-%d") if due else None),
            "created_by": created_by}


def run_tool(name: str, args: Dict, db: Session, ctx: Optional[Dict] = None):
    ctx = ctx or {}
    if name == "search_customers":
        return _search_customers(db, args.get("query", ""))
    if name == "get_customer":
        return _get_customer(db, args.get("account", ""))
    if name == "list_expiring":
        return _list_expiring(db, args.get("within_days", 60), args.get("ae"))
    if name == "aggregate":
        return _aggregate(db, args["group_by"], args["metric"],
                          args.get("filter_ae"), args.get("filter_status"))
    if name == "add_followup":
        return _add_followup(db, args.get("note", ""), args.get("account"), args.get("when"),
                             created_by=ctx.get("created_by"), creator_email=ctx.get("creator_email"))
    return {"error": f"unknown tool: {name}"}
