"""Function-calling tools for the chat agent — deterministic queries over the DB.

This is the "ฉลาดขึ้นมากๆ" layer: the model calls these tools instead of us
injecting raw rows and hoping it counts/sums correctly.
"""
import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import radar
from memory_manager import CustomerMemoryManager
from models import Customer, FollowUp
from notifications import compute_notifications, get_ae_emails
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
            "name": "list_customers",
            "description": ("รายชื่อลูกค้าตามเงื่อนไข — กรองตาม AE ผู้ดูแล และ/หรือ สถานะสัญญา. "
                            "ใช้ตอบ 'ลูกค้าของ Maprang มีใครบ้าง', 'ลูกค้า Active ของฉัน', 'ลูกค้าที่หมดอายุแล้วมีใครบ้าง'. "
                            "(ถ้าถามแค่จำนวน/มูลค่ารวม ใช้ aggregate แทน)"),
            "parameters": {
                "type": "object",
                "properties": {
                    "ae": {"type": "string", "description": "ชื่อ AE-IR เช่น Maprang (ไม่ระบุ = ทุกคน)"},
                    "status": {"type": "string", "description": "สถานะสัญญา เช่น Active, Expired, Cancelled (optional)"},
                    "expiry_state": {"type": "string", "enum": ["active", "expiring_soon", "expired"],
                                     "description": "กรองตามสถานะวันหมดอายุจริง (คำนวณจากวันนี้) — optional"},
                    "limit": {"type": "integer", "description": "จำนวนสูงสุด (default 30)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sales_opportunities",
            "description": ("Sales Radar — รายการโอกาสขายจัดอันดับให้แล้ว 3 โหมด: "
                            "retain=ลูกค้าใกล้หมดสัญญา+คะแนนเสี่ยงหลุด (ใช้ตอบ 'ใครเสี่ยงหลุด/ควรโฟกัสใครก่อน'), "
                            "winback=ลูกค้าที่ยกเลิก/หมดไปแล้วน่าดึงกลับ, "
                            "upsell=ลูกค้า IR ที่ยังไม่มี WD น่าเสนอเพิ่ม. คืน score+เหตุผลโปร่งใส"),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["retain", "winback", "upsell"]},
                    "ae": {"type": "string", "description": "กรองเฉพาะลูกค้าของ AE คนนี้ (optional)"},
                    "limit": {"type": "integer", "description": "จำนวนสูงสุด (default 10)"},
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_followup",
            "description": ("จัดการงานติดตามที่มีอยู่ — ทำเสร็จ / เลื่อนนัด / ลบ. "
                            "ใช้เมื่อผู้ใช้บอกว่า 'งาน...เสร็จแล้ว/ทำแล้ว', 'เลื่อนนัด...ไปพรุ่งนี้', 'ยกเลิกงาน...'. "
                            "ระบุ followup_id จากรายการ **งานค้าง** ใน system prompt (แต่ละงานมี [id:N])"),
            "parameters": {
                "type": "object",
                "properties": {
                    "followup_id": {"type": "integer", "description": "id ของงาน (จาก [id:N] ในรายการงานค้าง)"},
                    "action": {"type": "string", "enum": ["done", "reschedule", "delete"]},
                    "new_when": {"type": "string", "description": "วันเวลาใหม่ ISO เช่น '2026-07-17T14:00' (ใช้กับ reschedule)"},
                },
                "required": ["followup_id", "action"],
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
    {
        "type": "function",
        "function": {
            "name": "list_team",
            "description": ("รายชื่อทีมขาย (AE-IR) ทั้งหมดในระบบ พร้อมอีเมลที่ตั้งไว้และจำนวนลูกค้าที่ดูแล. "
                            "ใช้ตอบ 'คนในทีมมีใครบ้าง', 'ขออีเมลคนในทีม', 'AE แต่ละคนดูแลกี่ราย'"),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_about_customer",
            "description": ("บันทึก **ข้อเท็จจริงสำคัญที่เพิ่งรู้เกี่ยวกับลูกค้า** ลงสมุดความจำของทีม (ทุกคนเห็นรอบหน้า). "
                            "เรียกเองทันทีเมื่อผู้ใช้เล่าข้อมูลที่ควรจำข้ามครั้ง เช่น สถานะการต่อสัญญา ('ACE รอผ่านงบ Q3 ค่อยต่อ'), "
                            "ความสนใจสินค้า ('สนใจ WD เพิ่ม'), ผู้มีอำนาจตัดสินใจ, เหตุผลที่ยกเลิก, นัดสำคัญ. "
                            "**ห้าม**บันทึกข้อมูลที่อยู่ในฐานข้อมูลอยู่แล้ว (วันหมด/มูลค่า/ที่อยู่/อีเมล) — เก็บเฉพาะ insight ใหม่จากบทสนทนา"),
            "parameters": {
                "type": "object",
                "properties": {
                    "account": {"type": "string", "description": "account code ของลูกค้า เช่น ACE"},
                    "fact": {"type": "string", "description": "ข้อเท็จจริง 1 ข้อ กระชับ เป็นภาษาไทย เช่น 'รอผ่านงบ Q3 ค่อยตัดสินใจต่อสัญญา'"},
                },
                "required": ["account", "fact"],
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


# ── Fuzzy Thai matching (char-bigram — ภาษาไทยไม่เว้นวรรค word-token ใช้ไม่ได้) ──
def _bigrams(s: str) -> set:
    s = re.sub(r"[\s\.\-_/]+", "", (s or "").lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _bigram_sim(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _fuzzy_search(db: Session, query: str, limit: int = 5, min_sim: float = 0.25) -> List[Customer]:
    """fallback เมื่อ ilike หาไม่เจอ — สแกนทุก account/ชื่อบริษัท แล้วจัดอันดับความคล้าย."""
    scored = []
    for c in db.query(Customer).all():
        sim = max(_bigram_sim(query, c.account or ""),
                  _bigram_sim(query, c.company_name_th or ""),
                  _bigram_sim(query, c.company_name_en or ""))
        if sim >= min_sim:
            scored.append((sim, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]


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
    fuzzy = False
    if not rows and len(query.strip()) >= 3:
        rows = _fuzzy_search(db, query.strip())
        fuzzy = bool(rows)
    out = [customer_summary(c) for c in rows]
    if fuzzy:
        for d in out:
            d["fuzzy_match"] = True  # ชื่อไม่ตรงเป๊ะ — ให้บอทยืนยันกับผู้ใช้ว่าใช่รายนี้ไหม
    # แนบความจำของทีม (ถ้ามี) เพื่อให้บอทดึงมาตอบได้แม้จะค้นเจอผ่าน search
    for d in out:
        facts = CustomerMemoryManager.list_facts(db, d["account"])
        if facts:
            d["team_memory"] = facts
    return out


def _get_customer(db: Session, account: str) -> Optional[Dict]:
    c = db.get(Customer, account.strip().upper())
    if not c:
        return None
    d = customer_full(c)
    facts = CustomerMemoryManager.list_facts(db, c.account)
    if facts:
        d["team_memory"] = facts  # ความจำที่ทีมเคยบันทึกไว้ — ใช้ตอบเสริมได้
    return d


def _list_team(db: Session) -> List[Dict]:
    emails = get_ae_emails()
    rows = (db.query(Customer.ae_ir, func.count())
            .filter(Customer.ae_ir.isnot(None))
            .group_by(Customer.ae_ir).all())
    out = []
    for ae, cnt in rows:
        if not ae:
            continue
        out.append({"ae": ae, "email": emails.get(ae) or None, "customers": cnt})
    out.sort(key=lambda x: x["customers"], reverse=True)
    return out


def _remember_about_customer(db: Session, account: str, fact: str, created_by: Optional[str] = None) -> Dict:
    acct = (account or "").strip().upper()
    if not acct or not db.get(Customer, acct):
        return {"error": f"ไม่พบลูกค้า {acct}"}
    if not (fact or "").strip():
        return {"error": "fact ว่าง"}
    rec = CustomerMemoryManager.add(db, acct, fact, source="chat", created_by=created_by)
    return {"ok": True, "account": acct, "fact": rec.fact}


def _list_expiring(db: Session, within_days: int = 60, ae: Optional[str] = None) -> List[Dict]:
    return compute_notifications(db, ae=ae, threshold_days=within_days)


def _list_customers(db: Session, ae: Optional[str] = None, status: Optional[str] = None,
                    expiry_state_filter: Optional[str] = None, limit: int = 30) -> Dict:
    q = db.query(Customer)
    if ae:
        q = q.filter(Customer.ae_ir.ilike(f"%{ae.strip()}%"))
    if status:
        q = q.filter(func.lower(Customer.contract_status) == status.strip().lower())
    rows = q.order_by(Customer.expiry_date.is_(None), Customer.expiry_date.asc()).all()
    items = [customer_summary(c) for c in rows]
    if expiry_state_filter:
        items = [i for i in items if i["expiry_state"] == expiry_state_filter]
    total = len(items)
    limit = max(1, min(int(limit or 30), 100))
    return {"total": total, "showing": min(total, limit), "items": items[:limit]}


def _sales_opportunities(db: Session, mode: str, ae: Optional[str] = None, limit: int = 10) -> Dict:
    limit = max(1, min(int(limit or 10), 30))
    if mode == "retain":
        res = radar.retain(db, ae=ae, limit=limit)
    elif mode == "winback":
        res = radar.winback(db, ae=ae, limit=limit)
    elif mode == "upsell":
        res = radar.upsell(db, ae=ae, limit=limit)
    else:
        return {"error": f"invalid mode: {mode}"}
    res["mode"] = mode
    return res


def _manage_followup(db: Session, followup_id: int, action: str,
                     new_when: Optional[str] = None) -> Dict:
    f = db.get(FollowUp, int(followup_id))
    if not f:
        return {"error": f"ไม่พบงาน id {followup_id}"}
    note = f.note
    if action == "done":
        f.done = True
    elif action == "delete":
        db.delete(f)
        db.commit()
        return {"ok": True, "action": "delete", "note": note}
    elif action == "reschedule":
        due = None
        if new_when:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    due = datetime.strptime(new_when.strip()[:19], fmt)
                    break
                except ValueError:
                    continue
        if not due:
            return {"error": "new_when ไม่ถูกต้อง — ต้องเป็น ISO เช่น 2026-07-17T14:00"}
        f.due_date = due
        f.reminded = False  # เลื่อนนัดแล้วให้เตือนรอบใหม่ได้
    else:
        return {"error": f"invalid action: {action}"}
    db.commit()
    return {"ok": True, "action": action, "id": f.id, "note": note,
            "due": f.due_date.strftime("%Y-%m-%d %H:%M") if f.due_date else None,
            "done": f.done}


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
    f = FollowUp(account=acct, note=note, due_date=due, created_by=created_by,
                 creator_email=creator_email, source="chat")
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
    if name == "list_customers":
        return _list_customers(db, args.get("ae"), args.get("status"),
                               args.get("expiry_state"), args.get("limit", 30))
    if name == "sales_opportunities":
        return _sales_opportunities(db, args.get("mode", ""), args.get("ae"),
                                    args.get("limit", 10))
    if name == "manage_followup":
        return _manage_followup(db, args.get("followup_id", 0), args.get("action", ""),
                                args.get("new_when"))
    if name == "aggregate":
        return _aggregate(db, args["group_by"], args["metric"],
                          args.get("filter_ae"), args.get("filter_status"))
    if name == "add_followup":
        return _add_followup(db, args.get("note", ""), args.get("account"), args.get("when"),
                             created_by=ctx.get("created_by"), creator_email=ctx.get("creator_email"))
    if name == "list_team":
        return _list_team(db)
    if name == "remember_about_customer":
        return _remember_about_customer(db, args.get("account", ""), args.get("fact", ""),
                                        created_by=ctx.get("created_by"))
    return {"error": f"unknown tool: {name}"}
