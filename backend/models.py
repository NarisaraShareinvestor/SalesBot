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
