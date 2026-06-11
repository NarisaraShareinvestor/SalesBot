"""User memory (ported from hermes) — 1 record per user per memory_type.

  PROFILE  → {full_name, ae_name, role, ...}    ผู้ใช้คนนี้เป็น AE คนไหน
  CHAT     → {messages: [{role, message, timestamp}, ...]}  (rolling window)
  CUSTOM   → {notes: [{note, saved_at}, ...]}    สิ่งที่ user บอกให้ "จำไว้"
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models import MemoryType, UserMemory

CHAT_MAX_MESSAGES = 20


class UserMemoryManager:

    @staticmethod
    def _upsert(db: Session, user_email: str, memory_type: MemoryType, content: Dict[str, Any]) -> UserMemory:
        existing = db.query(UserMemory).filter(
            UserMemory.user_email == user_email,
            UserMemory.memory_type == memory_type,
            UserMemory.is_active == True,
        ).first()
        if existing:
            existing.content = content
            db.commit()
            db.refresh(existing)
            return existing
        record = UserMemory(user_email=user_email, memory_type=memory_type, content=content)
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def get_active_memory(db: Session, user_email: str, memory_type: MemoryType) -> Optional[UserMemory]:
        return db.query(UserMemory).filter(
            UserMemory.user_email == user_email,
            UserMemory.memory_type == memory_type,
            UserMemory.is_active == True,
        ).first()

    # ── PROFILE ────────────────────────────────────────────────────────────────
    @staticmethod
    def save_profile(db: Session, user_email: str, full_name: str = None, ae_name: str = None,
                     role: str = None) -> UserMemory:
        existing = UserMemoryManager.get_active_memory(db, user_email, MemoryType.PROFILE)
        content = dict(existing.content) if existing else {}
        if full_name is not None:
            content["full_name"] = full_name
        if ae_name is not None:
            content["ae_name"] = ae_name
        if role is not None:
            content["role"] = role
        content["saved_at"] = datetime.now().isoformat()
        return UserMemoryManager._upsert(db, user_email, MemoryType.PROFILE, content)

    # ── CHAT — rolling window of messages in 1 record ──────────────────────────
    @staticmethod
    def save_chat(db: Session, user_email: str, role: str, message: str) -> None:
        existing = UserMemoryManager.get_active_memory(db, user_email, MemoryType.CHAT)
        entry = {"role": role, "message": message, "timestamp": datetime.now().isoformat()}
        if existing:
            messages = list(existing.content.get("messages", []))
            messages.append(entry)
            if len(messages) > CHAT_MAX_MESSAGES:
                messages = messages[-CHAT_MAX_MESSAGES:]
            existing.content = {"messages": messages}
            db.commit()
        else:
            UserMemoryManager._upsert(db, user_email, MemoryType.CHAT, {"messages": [entry]})

    @staticmethod
    def get_chat_history(db: Session, user_email: str) -> List[Dict]:
        rec = UserMemoryManager.get_active_memory(db, user_email, MemoryType.CHAT)
        return rec.content.get("messages", []) if rec else []

    # ── CUSTOM — "จำไว้นะ" notes ───────────────────────────────────────────────
    @staticmethod
    def save_note(db: Session, user_email: str, note: str) -> None:
        existing = UserMemoryManager.get_active_memory(db, user_email, MemoryType.CUSTOM)
        entry = {"note": note, "saved_at": datetime.now().isoformat()}
        if existing:
            notes = list(existing.content.get("notes", []))
            notes.append(entry)
            existing.content = {"notes": notes}
            db.commit()
        else:
            UserMemoryManager._upsert(db, user_email, MemoryType.CUSTOM, {"notes": [entry]})

    @staticmethod
    def get_notes(db: Session, user_email: str) -> List[Dict]:
        rec = UserMemoryManager.get_active_memory(db, user_email, MemoryType.CUSTOM)
        return rec.content.get("notes", []) if rec else []
