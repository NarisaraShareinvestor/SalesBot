"""SalesBot models — Customer (contracts) + UserMemory (ported from hermes)."""
import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, Float, Integer, JSON, String, Text
from sqlalchemy.sql import func

from database import Base


class Customer(Base):
    """ลูกค้า/สัญญา IR — 1 แถวต่อ 1 account (merge จาก 2 ไฟล์ Excel)."""
    __tablename__ = "customers"

    account = Column(String(50), primary_key=True, index=True)

    company_name_th = Column(String(500), nullable=True)
    company_name_en = Column(String(500), nullable=True)

    ae_ir = Column(String(100), nullable=True, index=True)  # เจ้าของ sales (AE-IR)

    contract_type = Column(String(50), nullable=True)   # IR / IR+WD  (= "Product")
    period_months = Column(Integer, nullable=True)
    payment_cycle = Column(String(50), nullable=True)   # Monthly / Annually / ...

    monthly_payment = Column(Float, nullable=True)
    latest_value = Column(Float, nullable=True)         # = "มูลค่า (บาท)"

    effective_date = Column(DateTime, nullable=True)
    expiry_date = Column(DateTime, nullable=True, index=True)

    contract_status = Column(String(50), nullable=True, index=True)  # Active/Expired/Cancelled (อาจ stale)
    contract_status_text = Column(Text, nullable=True)               # remark

    market = Column(String(50), nullable=True)
    industry = Column(String(200), nullable=True)
    sector = Column(String(200), nullable=True)
    url = Column(String(500), nullable=True)

    address_th = Column(Text, nullable=True)
    address_en = Column(Text, nullable=True)
    zip_code = Column(String(20), nullable=True)

    # จาก PR-All-IR-List (join ด้วย account)
    grade = Column(String(20), nullable=True)
    ir_team = Column(String(100), nullable=True)
    contact_email = Column(String(255), nullable=True)
    cc_emails = Column(Text, nullable=True)

    extra = Column(JSON, nullable=True)  # คอลัมน์อื่นๆ ที่เหลือ

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Customer(account={self.account}, ae_ir={self.ae_ir})>"


class FollowUp(Base):
    """งานติดตาม / โน้ตต่อลูกค้า 1 ราย."""
    __tablename__ = "followups"

    id = Column(Integer, primary_key=True, index=True)
    account = Column(String(50), index=True, nullable=True)  # ผูกลูกค้า (ถ้ามี) — ว่างได้สำหรับงานทั่วไป
    note = Column(Text, nullable=False)
    due_date = Column(DateTime, nullable=True)
    done = Column(Boolean, default=False)
    created_by = Column(String(100), nullable=True)
    source = Column(String(20), default="manual")  # manual | chat
    is_shared = Column(Boolean, default=False)  # False=ส่วนตัว, True=แชร์ทีม
    created_at = Column(DateTime, default=func.now())


class CustomerMemory(Base):
    """ความจำเกี่ยวกับลูกค้า 1 ราย — ทีมเห็นร่วมกัน (institutional memory).

    AI ดึงข้อเท็จจริงจากแชทมาบันทึกอัตโนมัติ (source='chat') หรือผู้ใช้เพิ่มเอง (source='manual').
    รอบหน้าใครถามถึงลูกค้ารายนี้ บอทดึงความจำเหล่านี้มาเสริมได้ทันที.
    """
    __tablename__ = "customer_memory"

    id = Column(Integer, primary_key=True, index=True)
    account = Column(String(50), index=True, nullable=False)  # ลูกค้าที่ความจำนี้ผูกอยู่
    fact = Column(Text, nullable=False)                       # ข้อเท็จจริง 1 ข้อ
    source = Column(String(20), default="chat")               # chat | manual
    created_by = Column(String(100), nullable=True)           # ใครเป็นคนบันทึก/คุย
    created_at = Column(DateTime, default=func.now())
    is_active = Column(Boolean, default=True)


class IssueLog(Base):
    """บันทึกทุกบทสนทนา + auto-flag เคสที่บอทน่าจะตอบไม่ได้ — ใช้วิเคราะห์ปรับปรุงระบบ."""
    __tablename__ = "issue_log"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String(255), index=True, nullable=True)
    user_name = Column(String(120), nullable=True)
    ae = Column(String(100), nullable=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    tools_used = Column(JSON, nullable=True)
    flagged = Column(Boolean, default=False, index=True)   # น่าจะตอบไม่ได้/มีปัญหา
    flag_reason = Column(String(200), nullable=True)
    category = Column(String(120), nullable=True)          # เติมโดย nightly analysis
    created_at = Column(DateTime, default=func.now(), index=True)


class ImprovementReport(Base):
    """รายงานสรุปปัญหา + ข้อเสนอปรับปรุง ที่ระบบสร้างทุกเที่ยงคืน."""
    __tablename__ = "improvement_report"

    id = Column(Integer, primary_key=True, index=True)
    report_date = Column(String(10), index=True)   # YYYY-MM-DD (เวลาไทย)
    total_chats = Column(Integer, default=0)
    flagged_count = Column(Integer, default=0)
    summary = Column(Text, nullable=True)          # สรุปภาพรวมภาษาไทย
    categories = Column(JSON, nullable=True)        # [{category, count, examples}]
    engineer_actions = Column(Text, nullable=True)  # สิ่งที่วิศวกรต้องแก้ (tool/data/prompt)
    emailed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())


class LearnedGuidance(Base):
    """คำแนะนำที่ระบบเรียนรู้เอง (routing hint) — เสนอตอนเที่ยงคืน แต่ inactive จนวิศวกรอนุมัติ."""
    __tablename__ = "learned_guidance"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    source = Column(String(20), default="nightly")  # nightly | manual
    is_active = Column(Boolean, default=False, index=True)  # ต้องอนุมัติก่อนถึงจะ inject เข้า prompt
    report_date = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=func.now())


class MemoryType(str, enum.Enum):
    PROFILE = "profile"
    CHAT = "chat"
    CUSTOM = "custom"
    PREFERENCE = "preference"


class UserMemory(Base):
    """ความจำเกี่ยวกับผู้ใช้ (ตามหลัก hermes) — 1 record ต่อ user ต่อ memory_type."""
    __tablename__ = "user_memory"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String(255), index=True, nullable=False)
    memory_type = Column(Enum(MemoryType), nullable=False)
    content = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)
