"""Self-improvement loop — log issues, nightly analysis, learned guidance.

ปรัชญา (ปลอดภัย): ระบบ "วิเคราะห์ปัญหาเอง + เสนอทางแก้" ทุกเที่ยงคืน
แต่ **ไม่แก้โค้ด/ไม่เปลี่ยน prompt เองโดยอัตโนมัติ** — guidance ที่เสนอจะ inactive
จนกว่าวิศวกร (narisara.pa@shareinvestor.com) จะกดอนุมัติใน dashboard.
"""
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from models import ImprovementReport, IssueLog, LearnedGuidance

ADMIN_EMAIL = "narisara.pa@shareinvestor.com"
BANGKOK_OFFSET = timedelta(hours=7)
GUIDANCE_MAX_ACTIVE = 10  # กัน prompt บวม

# วลีที่บ่งว่าบอท "ยอมรับว่าตอบไม่ได้" — ใช้ triage (จับ confidently-wrong ไม่ได้)
_FAIL_PHRASES = ["ไม่มีข้อมูล", "ไม่มีในระบบ", "ไม่ได้อยู่ในระบบ", "ไม่ได้ถูกเก็บ",
                 "ไม่ได้บันทึก", "ไม่ได้ระบุ", "ขออภัย", "ไม่พบ", "ไม่สามารถ",
                 "ไม่แน่ใจ", "ไม่ทราบ", "ไม่เข้าใจ", "ตรวจสอบจากเอกสาร", "ระบบภายในองค์กร"]


def now_bkk() -> datetime:
    return datetime.utcnow() + BANGKOK_OFFSET


def today_bkk_str() -> str:
    return now_bkk().strftime("%Y-%m-%d")


# ── Logging (hot path — ต้องไม่ทำให้ /api/chat พัง) ───────────────────────────
def log_issue(db: Session, user_email, user_name, ae, question, answer, tools_used):
    try:
        reply = answer or ""
        flagged, reason = False, None
        if not reply.strip():
            flagged, reason = True, "บอทไม่ตอบ (empty)"
        else:
            for p in _FAIL_PHRASES:
                if p in reply:
                    flagged, reason = True, f"พบวลี '{p}'"
                    break
        rec = IssueLog(user_email=user_email, user_name=user_name, ae=ae,
                       question=question, answer=reply, tools_used=tools_used or [],
                       flagged=flagged, flag_reason=reason)
        db.add(rec)
        db.commit()
    except Exception:
        db.rollback()  # logging ห้ามทำให้แชทล่ม


# ── Learned guidance (inject เข้า prompt เฉพาะที่ active) ──────────────────────
def get_active_guidance(db: Session) -> list:
    try:
        rows = (db.query(LearnedGuidance)
                .filter(LearnedGuidance.is_active == True)  # noqa: E712
                .order_by(LearnedGuidance.created_at.desc())
                .limit(GUIDANCE_MAX_ACTIVE).all())
        return [r.text for r in rows]
    except Exception:
        return []


# ── Email (best-effort — Microsoft 365 อาจบล็อก, ห้ามทำให้ job ล่ม) ───────────
def _send_report_email(report: ImprovementReport) -> bool:
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    if not (host and user and pwd):
        return False
    try:
        body = (f"SalesBot — รายงานปรับปรุงระบบ {report.report_date}\n\n"
                f"บทสนทนาทั้งหมด: {report.total_chats} | flagged: {report.flagged_count}\n\n"
                f"== สรุป ==\n{report.summary or '-'}\n\n"
                f"== สิ่งที่วิศวกรควรแก้ ==\n{report.engineer_actions or '-'}\n\n"
                f"ดูรายละเอียด + อนุมัติ guidance ที่: https://salesbot.ohmai.me (เมนู Admin)")
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"[SalesBot] รายงานปรับปรุงระบบ {report.report_date}"
        msg["From"] = os.getenv("SMTP_FROM", user)
        msg["To"] = ADMIN_EMAIL
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception:
        return False


# ── Nightly analysis ─────────────────────────────────────────────────────────
def _llm():
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=key) if key else None


def run_nightly(db: Session, force: bool = False) -> dict:
    """รวบรวมปัญหา 24 ชม.ที่ผ่านมา → วิเคราะห์ด้วย LLM → บันทึกรายงาน + เสนอ guidance (inactive).

    Idempotent ต่อวัน (เวลาไทย): ถ้ามีรายงานวันนี้แล้วจะไม่รันซ้ำ เว้นแต่ force=True.
    """
    date_str = today_bkk_str()
    existing = db.query(ImprovementReport).filter(
        ImprovementReport.report_date == date_str).first()
    if existing and not force:
        return {"skipped": True, "reason": "มีรายงานของวันนี้แล้ว", "report_date": date_str}

    # ช่วงเวลา: 24 ชม.ที่ผ่านมา (UTC ใน DB)
    since_utc = datetime.utcnow() - timedelta(hours=24)
    logs = (db.query(IssueLog)
            .filter(IssueLog.created_at >= since_utc)
            .order_by(IssueLog.created_at.asc()).all())
    total = len(logs)
    flagged = [l for l in logs if l.flagged]

    if not flagged:
        summary = f"ช่วง 24 ชม.ที่ผ่านมา มีบทสนทนา {total} ครั้ง ไม่พบเคสที่บอทตอบไม่ได้ — ระบบทำงานปกติ"
        report = _save_report(db, existing, date_str, total, 0, summary, [], "ไม่มีสิ่งที่ต้องแก้")
        report.emailed = _send_report_email(report)
        db.commit()
        return _report_dict(report, proposed=0)

    # เตรียมข้อมูลให้ LLM (จำกัดจำนวนตัวอย่าง)
    sample = [{"q": l.question, "a": (l.answer or "")[:300], "tools": l.tools_used,
               "why": l.flag_reason} for l in flagged[:40]]

    client = _llm()
    categories, summary, actions, guidance = [], "", "", []
    if client:
        import json
        prompt = (
            "คุณคือวิศวกร AI ที่ดูแล SalesBot (ผู้ช่วยขาย IR). ด้านล่างคือเคสที่บอท 'น่าจะตอบไม่ได้' "
            "ในรอบ 24 ชม. (q=คำถามผู้ใช้, a=คำตอบบอท, tools=tool ที่เรียก, why=เหตุที่ถูก flag).\n\n"
            f"{json.dumps(sample, ensure_ascii=False)}\n\n"
            "วิเคราะห์แล้วตอบเป็น JSON เท่านั้น รูปแบบ:\n"
            "{\n"
            '  "summary": "สรุปภาพรวมปัญหา 2-4 บรรทัด ภาษาไทย",\n'
            '  "categories": [{"category":"ชื่อกลุ่มปัญหา","count":จำนวน,"example":"ตัวอย่างคำถาม"}],\n'
            '  "engineer_actions": "สิ่งที่วิศวกรต้องลงมือแก้ (เพิ่ม tool/ข้อมูล/แก้ prompt) เป็น bullet - ... ชัดเจน",\n'
            '  "auto_guidance": ["คำแนะนำสั้นๆ ที่ปรับ prompt ได้ทันทีโดยไม่ต้องแก้โค้ด เช่นการ route คำถาม (0-3 ข้อ ถ้าไม่มีให้ [])"]\n'
            "}\n"
            "หมายเหตุ: ปัญหาที่ต้องเพิ่ม tool/ข้อมูลใหม่ → ใส่ใน engineer_actions ไม่ใช่ auto_guidance. "
            "auto_guidance ใช้เฉพาะ hint การตอบ/การเลือก tool ที่มีอยู่แล้วเท่านั้น"
        )
        try:
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                messages=[{"role": "system", "content": "ตอบเป็น JSON ภาษาไทยเท่านั้น"},
                          {"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            summary = data.get("summary", "")
            categories = data.get("categories", [])
            actions = data.get("engineer_actions", "")
            guidance = [g for g in data.get("auto_guidance", []) if isinstance(g, str) and g.strip()][:3]
        except Exception as e:
            summary = f"พบ {len(flagged)} เคสที่บอทตอบไม่ได้ (วิเคราะห์อัตโนมัติไม่สำเร็จ: {e})"
            actions = "ตรวจสอบเคส flagged ใน dashboard ด้วยตนเอง"
    else:
        summary = f"พบ {len(flagged)} เคสที่บอทตอบไม่ได้ (ไม่ได้ตั้งค่า OpenAI key)"
        actions = "ตรวจสอบเคส flagged ใน dashboard"

    report = _save_report(db, existing, date_str, total, len(flagged), summary, categories, actions)

    # เสนอ guidance — บันทึกแบบ INACTIVE รออนุมัติ
    proposed = 0
    for g in guidance:
        db.add(LearnedGuidance(text=g, source="nightly", is_active=False, report_date=date_str))
        proposed += 1
    db.commit()

    report.emailed = _send_report_email(report)
    db.commit()
    return _report_dict(report, proposed=proposed)


def _save_report(db, existing, date_str, total, flagged_count, summary, categories, actions):
    if existing:
        existing.total_chats = total
        existing.flagged_count = flagged_count
        existing.summary = summary
        existing.categories = categories
        existing.engineer_actions = actions
        db.commit()
        db.refresh(existing)
        return existing
    rec = ImprovementReport(report_date=date_str, total_chats=total, flagged_count=flagged_count,
                            summary=summary, categories=categories, engineer_actions=actions)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def _report_dict(r: ImprovementReport, proposed: int = 0) -> dict:
    return {"report_date": r.report_date, "total_chats": r.total_chats,
            "flagged_count": r.flagged_count, "summary": r.summary,
            "categories": r.categories, "engineer_actions": r.engineer_actions,
            "emailed": r.emailed, "proposed_guidance": proposed}
