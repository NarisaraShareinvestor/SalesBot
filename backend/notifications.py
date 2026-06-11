"""Notifications — compute expiring contracts (in-app) + email digest per AE."""
import json
import os
import smtplib
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from models import Customer
from services import EXPIRING_THRESHOLD_DAYS, customer_summary, expiry_state

# ── AE → email mapping (แก้ได้ผ่านไฟล์หรือ UI) ───────────────────────────────
AE_EMAIL_FILE = Path(__file__).resolve().parent / "ae_emails.json"


def get_ae_emails() -> Dict[str, str]:
    if AE_EMAIL_FILE.exists():
        try:
            return json.loads(AE_EMAIL_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def set_ae_emails(mapping: Dict[str, str]) -> Dict[str, str]:
    clean = {k: (v or "").strip() for k, v in mapping.items() if (v or "").strip()}
    AE_EMAIL_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


# ── Per-user SMTP credentials (กรอกตอน login — ของแต่ละคนแยกกัน) ──────────────
# เก็บใน user_smtp.json (gitignored). หมายเหตุ: เก็บแบบ plaintext เหมาะกับใช้ภายใน — แนะนำใช้ App Password (เพิกถอนได้)
USER_SMTP_FILE = Path(__file__).resolve().parent / "user_smtp.json"


def _load_user_smtp() -> Dict[str, Dict]:
    if USER_SMTP_FILE.exists():
        try:
            return json.loads(USER_SMTP_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def set_user_smtp(email: str, password: str, smtp_user: str = None,
                  smtp_from: str = None, host: str = None, port: int = None) -> bool:
    data = _load_user_smtp()
    email = (email or "").strip()
    if not email or not (password or "").strip():
        return False
    data[email] = {
        "user": (smtp_user or email).strip(),
        "password": password.strip(),
        "from": (smtp_from or email).strip(),
        "host": (host or "").strip() or None,
        "port": port,
    }
    USER_SMTP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def has_user_smtp(email: str) -> bool:
    u = _load_user_smtp().get((email or "").strip())
    return bool(u and u.get("password"))


# สัญญาที่ยกเลิกไปแล้ว = ลูกค้าเลิกใช้ ไม่ใช่งานต่ออายุ → ไม่นับเป็นการแจ้งเตือน
_DEAD_STATUSES = {"cancelled"}


def compute_notifications(db: Session, ae: Optional[str] = None,
                          threshold_days: int = EXPIRING_THRESHOLD_DAYS) -> List[Dict]:
    """สัญญาที่ใกล้หมดอายุ (ยังไม่เลย) — ตัด Cancelled และที่หมดแล้วออก. เรียงด่วนสุดก่อน."""
    q = db.query(Customer).filter(Customer.expiry_date.isnot(None))
    if ae:
        q = q.filter(Customer.ae_ir == ae)
    items = []
    for c in q.all():
        if (c.contract_status or "").strip().lower() in _DEAD_STATUSES:
            continue
        es = expiry_state(c.expiry_date, threshold_days)
        if es["state"] == "expiring_soon":
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


# ── Email ──────────────────────────────────────────────────────────────────
def _smtp_config(sender_email: Optional[str] = None):
    """ค่า SMTP — ใช้ของผู้ใช้ที่ล็อกอิน (ถ้ามี) ก่อน, ไม่งั้น fallback ไป .env ส่วนกลาง."""
    cfg = {
        "host": os.getenv("SMTP_HOST") or "smtp.office365.com",
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASS"),
        "from_addr": os.getenv("SMTP_FROM") or os.getenv("SMTP_USER"),
    }
    if sender_email:
        u = _load_user_smtp().get(sender_email.strip())
        if u and u.get("password"):
            cfg["host"] = u.get("host") or cfg["host"]
            cfg["port"] = int(u.get("port") or cfg["port"])
            cfg["user"] = u.get("user") or sender_email
            cfg["password"] = u["password"]
            cfg["from_addr"] = u.get("from") or u.get("user") or sender_email
    return cfg


def _build_digest_html(ae: str, items: List[Dict]) -> str:
    parts = []
    for i in items:
        when = "หมดอายุแล้ว" if i["expiry_state"] == "expired" else f"อีก {i['days_to_expiry']} วัน"
        parts.append(
            f"<tr><td>{i['account']}</td><td>{i['company_name'] or '-'}</td>"
            f"<td>{i['expiry_date'] or '-'}</td><td>{when}</td>"
            f"<td>{i['value'] or '-'}</td></tr>"
        )
    rows = "".join(parts)
    return f"""<h3>SalesBot — สรุปสัญญาใกล้หมด/หมดอายุ (AE: {ae})</h3>
<p>มีทั้งหมด {len(items)} รายการ ณ วันที่ {datetime.now():%Y-%m-%d}</p>
<table border="1" cellpadding="6" cellspacing="0">
<tr><th>Account</th><th>บริษัท</th><th>วันหมดอายุ</th><th>สถานะ</th><th>มูลค่า</th></tr>
{rows}</table>"""


def send_expiry_digest(db: Session, to_email: Optional[str] = None,
                       ae: Optional[str] = None, sender_email: Optional[str] = None) -> Dict:
    """ส่งอีเมลสรุปให้ AE แต่ละคนตามอีเมลที่ map ไว้ (หรือ override ด้วย to_email/ae)."""
    cfg = _smtp_config(sender_email)
    items = compute_notifications(db, ae)
    if not items:
        return {"sent": 0, "skipped": True, "reason": "ไม่มีสัญญาใกล้หมดอายุ"}

    by_ae = defaultdict(list)
    for i in items:
        by_ae[i["ae_ir"] or "Unassigned"].append(i)

    ae_emails = get_ae_emails()
    smtp_ready = bool(cfg["host"] and cfg["user"] and cfg["password"])

    # หาปลายทางของแต่ละ AE: to_email (override) > อีเมลที่ map ไว้
    targets, missing = [], []
    scope = [ae] if ae else list(by_ae.keys())
    for a in scope:
        a_items = by_ae.get(a, [])
        if not a_items:
            continue
        recipient = to_email or ae_emails.get(a)
        if not recipient:
            missing.append(a)
        else:
            targets.append((a, recipient, a_items))

    if not smtp_ready:
        return {"sent": 0, "skipped": True,
                "reason": "ยังไม่ได้ตั้งค่า SMTP (กรอก SMTP_HOST/SMTP_USER/SMTP_PASS ใน .env)",
                "preview": {a: len(v) for a, v in by_ae.items()}, "missing_email": missing}
    if not targets:
        return {"sent": 0, "skipped": True,
                "reason": "ยังไม่ได้ตั้งอีเมลของ AE — ไปที่ 'ตั้งค่าอีเมลทีม' ก่อนค่ะ",
                "missing_email": missing}

    sent = []
    with smtplib.SMTP(cfg["host"], cfg["port"]) as s:
        s.starttls()
        s.login(cfg["user"], cfg["password"])
        for ae_name, recipient, a_items in targets:
            msg = MIMEText(_build_digest_html(ae_name, a_items), "html", "utf-8")
            msg["Subject"] = f"[SalesBot] สัญญาใกล้หมด/หมดอายุ ({len(a_items)} ราย) — {ae_name}"
            msg["From"] = cfg["from_addr"]
            msg["To"] = recipient
            s.sendmail(cfg["from_addr"], [recipient], msg.as_string())
            sent.append({"ae": ae_name, "to": recipient, "count": len(a_items)})
    return {"sent": len(sent), "skipped": False, "recipients": sent, "missing_email": missing}


# ── Calendar invite (.ics METHOD:REQUEST) → ลงปฏิทิน Outlook/M365 ────────────
_TZ_BLOCK = (
    "BEGIN:VTIMEZONE\r\nTZID:Asia/Bangkok\r\nBEGIN:STANDARD\r\n"
    "DTSTART:19700101T000000\r\nTZOFFSETFROM:+0700\r\nTZOFFSETTO:+0700\r\n"
    "TZNAME:+07\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
)


def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(summary, start, all_day=False, duration_min=30, description="", location="",
              organizer="", attendee="", uid="") -> str:
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    if all_day:
        ds = "DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d")
        de = "DTEND;VALUE=DATE:" + (start + timedelta(days=1)).strftime("%Y%m%d")
    else:
        end = start + timedelta(minutes=duration_min)
        ds = "DTSTART;TZID=Asia/Bangkok:" + start.strftime("%Y%m%dT%H%M%S")
        de = "DTEND;TZID=Asia/Bangkok:" + end.strftime("%Y%m%dT%H%M%S")
    lines = [
        "BEGIN:VCALENDAR", "PRODID:-//SalesBot//IR Sales//TH", "VERSION:2.0",
        "CALSCALE:GREGORIAN", "METHOD:REQUEST", _TZ_BLOCK.rstrip("\r\n"),
        "BEGIN:VEVENT", f"UID:{uid or dtstamp}", f"DTSTAMP:{dtstamp}", ds, de,
        f"SUMMARY:{_ics_escape(summary)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_ics_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_ics_escape(location)}")
    if organizer:
        lines.append(f"ORGANIZER;CN=SalesBot:mailto:{organizer}")
    if attendee:
        lines.append(f"ATTENDEE;CN={attendee};RSVP=TRUE;PARTSTAT=NEEDS-ACTION;ROLE=REQ-PARTICIPANT:mailto:{attendee}")
    lines += ["SEQUENCE:0", "STATUS:CONFIRMED", "TRANSP:OPAQUE", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines)


def send_calendar_invite(to_email, summary, start, all_day=False, duration_min=30,
                         description="", location="", uid="", sender_email=None) -> Dict:
    """ส่งคำเชิญปฏิทิน (.ics) ทางอีเมล → Outlook/Teams เด้งขึ้นปฏิทิน. ใช้บัญชีของ sender_email ถ้ามี."""
    cfg = _smtp_config(sender_email)
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        return {"sent": False, "reason": "SMTP not configured"}
    if not to_email:
        return {"sent": False, "reason": "no recipient"}

    ics = build_ics(summary, start, all_day, duration_min, description, location,
                    organizer=cfg["from_addr"], attendee=to_email, uid=uid)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[SalesBot] {summary}"
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_email
    when = start.strftime("%d/%m/%Y") if all_day else start.strftime("%d/%m/%Y %H:%M น.")
    msg.attach(MIMEText(f"นัดหมายจาก SalesBot: {summary}\nเวลา: {when}\n{description}", "plain", "utf-8"))
    cal = MIMEText(ics, "calendar;method=REQUEST", "utf-8")
    msg.attach(cal)
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"]) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.sendmail(cfg["from_addr"], [to_email], msg.as_string())
        return {"sent": True, "to": to_email, "when": when}
    except Exception as e:  # noqa: BLE001 — รายงานกลับให้ผู้ใช้ ไม่ให้ล้มทั้ง request
        return {"sent": False, "reason": str(e)[:160]}
