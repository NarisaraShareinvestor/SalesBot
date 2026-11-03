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
from services import STAGES, customer_full, customer_summary, lifecycle

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
            "description": ("รายชื่อลูกค้าตามเงื่อนไข — กรองตาม AE ผู้ดูแล และ/หรือ stage. "
                            "ใช้ตอบ 'ลูกค้าของ Maprang มีใครบ้าง', 'ลูกค้า Active ของฉัน', 'ลูกค้าที่หมดอายุแล้วมีใครบ้าง'. "
                            "(ถ้าถามแค่จำนวน/มูลค่ารวม ใช้ aggregate แทน)"),
            "parameters": {
                "type": "object",
                "properties": {
                    "ae": {"type": "string", "description": "ชื่อ AE-IR เช่น Maprang (ไม่ระบุ = ทุกคน, 'ไม่ระบุ' = ยังไม่มีคนดูแล)"},
                    "stage": {"type": "string", "enum": list(STAGES),
                              "description": "active=ใช้งานอยู่, expiring_soon=ใกล้หมด, expired=หมดอายุยังไม่ต่อ, cancelled=ยกเลิก, pending=กำลังดำเนินการ, unknown=ไม่มีวันหมด"},
                    "status_stale_only": {"type": "boolean", "description": "true = เฉพาะรายที่ในระบบยังเขียน Active แต่หมดอายุแล้ว (ต้องอัปเดต/ตามต่อ)"},
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
            "description": ("นับจำนวนหรือรวม/เฉลี่ยมูลค่าสัญญา (ต่อปี) แบ่งกลุ่มตามมิติที่เลือก — ใช้ stage ชุดเดียวกับหน้า Dashboard. "
                            "ใช้ตอบ 'ลูกค้าของ Maprang active กี่ราย', 'รวมมูลค่าสัญญาแต่ละ AE', 'แต่ละสถานะมีกี่ราย'"),
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {"type": "string", "enum": ["stage", "ae", "industry", "market", "contract_type", "grade"]},
                    "metric": {"type": "string", "enum": ["count", "sum_value", "avg_value"]},
                    "filter_ae": {"type": "string", "description": "กรองเฉพาะ AE คนนี้ (optional)"},
                    "filter_stage": {"type": "string", "enum": list(STAGES), "description": "กรองเฉพาะ stage (optional)"},
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
            "name": "portfolio_overview",
            "description": ("ภาพรวมพอร์ตทั้งทีม (ตัวเลขชุดเดียวกับหน้า Dashboard): จำนวนตาม stage, มูลค่าสัญญาในมือ, "
                            "มูลค่าที่ครบกำหนดใน 0-30/31-60/61-90 วัน, รายที่หลุดใน 90 วันที่ผ่านมา, รายที่สถานะในระบบค้าง, "
                            "และภาระของ AE แต่ละคน. ใช้ตอบคำถามผู้จัดการ เช่น 'ภาพรวมทีมเป็นยังไง', "
                            "'เดือนหน้าจะหลุดเท่าไร', 'ใครงานหนักสุด', 'มีอะไรต้องรีบจัดการ'"),
            "parameters": {"type": "object", "properties": {}},
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

_GROUP_KEY = {
    "ae": lambda c, lc: c.ae_ir,
    "stage": lambda c, lc: lc["stage_label"],
    "industry": lambda c, lc: c.industry,
    "market": lambda c, lc: c.market,
    "contract_type": lambda c, lc: c.contract_type,
    "grade": lambda c, lc: c.grade,
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
    open_tasks = (db.query(FollowUp).filter(FollowUp.account == c.account, FollowUp.done == False)  # noqa: E712
                  .order_by(FollowUp.due_date.is_(None), FollowUp.due_date.asc()).all())
    if open_tasks:
        d["open_followups"] = [{"id": f.id, "note": f.note, "created_by": f.created_by,
                                "due": f.due_date.strftime("%Y-%m-%d %H:%M") if f.due_date else None}
                               for f in open_tasks]
    return d


def _portfolio_overview(db: Session) -> Dict:
    from services import dashboard_data
    d = dashboard_data(db)
    o = radar.renewal_outlook(db, limit=5)
    by_ae = [{k: r[k] for k in ("ae", "active", "expiring_soon", "expired", "total_value")}
             for r in d["by_ae"] if r["active"] + r["expiring_soon"] + r["expired"]]
    keep = ("account", "company_name", "ae_ir", "value", "days_to_expiry")
    lo, buckets = 0, []
    for b in o["buckets"]:  # buckets don't overlap — say so, and give the running total
        buckets.append({"range_days": f"{lo}-{b['within']}", "count": b["count"], "value": b["value"]})
        lo = b["within"] + 1
    return {"stages": d["stages"], "totals": d["totals"], "by_ae": by_ae,
            "due_next_90_days_total": {"count": sum(b["count"] for b in buckets), "value": sum(b["value"] for b in buckets)},
            "due_by_range": buckets, "call_first": [{k: u[k] for k in keep} | {"risk": u["risk_level"]} for u in o["upcoming"]],
            "lapsed_90d": {"count": o["lapsed_total"], "value": o["lapsed_value"],
                           "top": [{k: u[k] for k in keep} for u in o["lapsed"]]},
            "note": "total_value = มูลค่าต่อปีของ stage active+expiring_soon+expired (ไม่นับยกเลิก)"}


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


def _ae_filter(q, ae: Optional[str]):
    if not ae:
        return q
    if ae.strip() == "ไม่ระบุ":
        return q.filter(Customer.ae_ir.is_(None))
    return q.filter(Customer.ae_ir.ilike(f"%{ae.strip()}%"))


def _list_customers(db: Session, ae: Optional[str] = None, stage: Optional[str] = None,
                    stale_only: bool = False, limit: int = 30) -> Dict:
    q = _ae_filter(db.query(Customer), ae)
    rows = q.order_by(Customer.expiry_date.is_(None), Customer.expiry_date.asc()).all()
    items = [customer_summary(c) for c in rows]
    if stage:
        items = [i for i in items if i["stage"] == stage]
    if stale_only:
        items = [i for i in items if i["status_stale"]]
    total = len(items)
    limit = max(1, min(int(limit or 30), 100))
    return {"total": total, "total_value": round(sum(i["value"] or 0 for i in items)),
            "showing": min(total, limit), "items": items[:limit]}


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
               filter_ae: Optional[str] = None, filter_stage: Optional[str] = None) -> List[Dict]:
    key_of = _GROUP_KEY.get(group_by)
    if key_of is None:
        return [{"error": f"invalid group_by: {group_by}"}]
    if metric not in ("count", "sum_value", "avg_value"):
        return [{"error": f"invalid metric: {metric}"}]

    groups: Dict[str, List[float]] = {}
    for c in _ae_filter(db.query(Customer), filter_ae).all():
        lc = lifecycle(c)
        if filter_stage and lc["stage"] != filter_stage:
            continue
        groups.setdefault(key_of(c, lc) or "ไม่ระบุ", []).append(c.latest_value or 0)

    out = []
    for key, values in groups.items():
        if metric == "count":
            v = len(values)
        elif metric == "sum_value":
            v = round(sum(values), 2)
        else:
            v = round(sum(values) / len(values), 2)
        out.append({"group": key, metric: v})
    out.sort(key=lambda r: r[metric], reverse=True)
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
        return _list_customers(db, args.get("ae"), args.get("stage"),
                               bool(args.get("status_stale_only")), args.get("limit", 30))
    if name == "sales_opportunities":
        return _sales_opportunities(db, args.get("mode", ""), args.get("ae"),
                                    args.get("limit", 10))
    if name == "manage_followup":
        return _manage_followup(db, args.get("followup_id", 0), args.get("action", ""),
                                args.get("new_when"))
    if name == "aggregate":
        return _aggregate(db, args["group_by"], args["metric"],
                          args.get("filter_ae"), args.get("filter_stage"))
    if name == "add_followup":
        return _add_followup(db, args.get("note", ""), args.get("account"), args.get("when"),
                             created_by=ctx.get("created_by"), creator_email=ctx.get("creator_email"))
    if name == "list_team":
        return _list_team(db)
    if name == "portfolio_overview":
        return _portfolio_overview(db)
    if name == "remember_about_customer":
        return _remember_about_customer(db, args.get("account", ""), args.get("fact", ""),
                                        created_by=ctx.get("created_by"))
    return {"error": f"unknown tool: {name}"}
