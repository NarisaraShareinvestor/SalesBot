# SalesBot — IR Sales Intelligence Assistant

ผู้ช่วย AI สำหรับทีม IR Sales ของ ShareInvestor — แชทถามข้อมูลลูกค้า/สัญญาแบบภาษาธรรมชาติ,
ดู/เพิ่ม/แก้ข้อมูล, และแจ้งเตือนสัญญาใกล้หมดอายุ สร้างตามหลัก "ความจำ" ของ hermes
และฉลาดขึ้นด้วย **function-calling** บนฐานข้อมูลจริง

UI โทนเทา-ขาว เรียบหรู ไม่มี emoji ใช้ไอคอนเส้น (SVG) ทุกคนในทีมเข้าถึงและแก้ไขได้

## ฟีเจอร์
1. **Dashboard / มุมมองทีม** — การ์ดสรุป (จำนวนลูกค้า, มูลค่ารวม, Active, ใกล้หมด, หมดอายุ, ยังไม่มอบหมาย)
   + ตาราง "ภาพรวมตาม AE" (Lead ใช้ดูการมอบหมายงานของแต่ละคน) คลิกชื่อ AE → กรองดูลูกค้าของคนนั้น
2. **Chat อัจฉริยะ** — gpt-4o + tool-calling เรียก query จริงบน DB (ไม่เดา/ไม่นับเอง)
   - อ่าน: `search_customers` (ชื่อ/อีเมล/cc), `get_customer`, `list_expiring`, `aggregate`
   - **เขียน**: `add_followup` — สั่ง "ลงตาราง วันนี้ 14:00 โทรหา ACE" → บันทึกงานลงระบบ (ดูได้ที่หน้า Notifications)
   - **ความจำ**: จำว่าผู้ใช้คือใคร (อีเมลจริงจากการ login) + AE คนไหน → คำถามคลุมเครือ ("ลูกค้าที่ฉันดูแล") ตอบเฉพาะของ AE นั้น;
     จำ chat history (rolling); จำโน้ตจากคำว่า "จำไว้นะ ..."
   - **Login**: หน้าแรกกรอกอีเมล/ชื่อ/AE/บทบาท → memory ผูกกับอีเมลจริงของแต่ละคน (เก็บใน localStorage + PROFILE)
3. **Data** — ตารางลูกค้า (ค้นหา/กรอง/แบ่งหน้า), แท็บ "สัญญาใกล้หมดอายุ" / "IR Contact",
   ฟอร์มเพิ่ม/แก้ (รวม **มอบหมาย/เปลี่ยน AE**), **Export CSV** ตามตัวกรอง,
   และ **Follow-up / โน้ตต่อลูกค้า** (งานติดตาม + กำหนดวัน + ติ๊กเสร็จ) ในหน้าต่างแก้ไข
4. **Notifications (หน้าเต็ม)** — รวมงานทั้งหมดไว้ในเว็บ: งานติดตาม (Follow-up/จาก Chat) + สัญญาใกล้หมด/หมดอายุ
   (คำนวณจาก **วันหมดอายุเทียบวันนี้**, ตัด Cancelled), มี filter (วันนี้ / 7 วันข้างหน้า / ใกล้หมด / Follow-up / จาก Chat),
   ปุ่ม "สร้างงานใหม่", ทำเสร็จ/เลื่อน, สรุปงานของคุณ + ปฏิทินงาน — แสดงในเว็บล้วน (ไม่พึ่งอีเมล/ปฏิทินภายนอก)
5. **User switcher** — เลือกตัวตน (AE แต่ละคน หรือ "ทีม (Lead)") ที่มุมซ้ายล่าง;
   มีผลต่อ default ของแจ้งเตือน/แชท/แท็บใกล้หมด แต่ทุกคนยังดู-แก้ข้อมูลได้ทั้งหมด

## โครงสร้าง
```
backend/
  database.py        engine/session (SQLite default, Postgres-ready)
  models.py          Customer + UserMemory
  memory_manager.py  ความจำผู้ใช้ (PROFILE/CHAT/CUSTOM)
  services.py        คำนวณ expiry + serialize ลูกค้า (ใช้ร่วมกัน)
  chat_tools.py      นิยาม tool + executor (query DB)
  notifications.py   คำนวณใกล้หมด + ส่งอีเมล
  import_data.py     อ่าน Excel 2 ไฟล์ → seed salesbot.db (idempotent)
  main.py            FastAPI: chat, customer CRUD, notifications, เสิร์ฟ frontend
frontend/index.html  SPA (Chat + Data + กระดิ่งแจ้งเตือน)
Data/                ไฟล์ Excel ต้นทาง
```

## ติดตั้ง & รัน
```bash
cp .env.example .env        # ใส่ OPENAI_API_KEY
./run.sh                    # setup venv + seed DB + เปิดเซิร์ฟเวอร์
# เปิด http://localhost:8000
```
หรือทำเอง:
```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd backend && ../.venv/bin/python import_data.py        # seed ครั้งเดียว
../.venv/bin/python -m uvicorn main:app --reload --port 8000
```

## ข้อมูล
Import ครั้งเดียวจาก `Data/` เข้า DB (DB เป็น source of truth — เพิ่ม/แก้ผ่านแอป):
- `IR-Agreement-Status 2025-2.xlsx` = สัญญาหลัก (collapse เหลือ 1 แถว/account โดยเลือกสัญญาที่หมดอายุล่าสุด)
- `20260512-PR-All-IR-List.xlsx` = ผู้ติดต่อ IR (join ด้วย Account → Grade, IR Team, Email, cc)

รัน `import_data.py` ซ้ำได้ทุกเมื่อ (upsert) เมื่ออัปเดต Excel ต้นทาง

## API หลัก
| Method | Path | หน้าที่ |
|---|---|---|
| POST | `/api/chat` | แชท (function-calling + memory) |
| GET | `/api/customers` | ตาราง (search/filter/paginate) |
| GET | `/api/customers/expiring` | สัญญาใกล้หมด/หมดอายุ |
| GET | `/api/customers/contacts` | IR Contact |
| GET/POST/PUT | `/api/customers[/{account}]` | ดู/เพิ่ม/แก้ |
| GET | `/api/dashboard` | สรุปทั้งทีม + แยกตาม AE |
| GET | `/api/aes` | รายชื่อ AE (switcher + dropdown มอบหมาย) |
| GET | `/api/export/customers` | ดาวน์โหลด CSV (ตามตัวกรอง) |
| GET | `/api/followups?ae=` | งานติดตามทั้งทีม (เห็นสิ่งที่สั่งลงตารางผ่านแชท) |
| GET/POST | `/api/customers/{account}/followups` | งานติดตามของลูกค้า |
| PUT/DELETE | `/api/followups/{id}` | แก้/ลบงานติดตาม |
| GET/PUT | `/api/ae-emails` | mapping AE → อีเมล (ตั้งค่าอีเมลทีม) |
| GET | `/api/notifications?ae=` | รายการ + count กระดิ่ง |
| POST | `/api/notifications/send-email` | ส่งสรุปทางอีเมล |
| GET/POST | `/api/profile` | ตั้งว่าผู้ใช้คือ AE คนไหน |

## หมายเหตุ
- ผู้ใช้ปัจจุบัน (demo) ตั้งใน `frontend/index.html` ที่ตัวแปร `USER` (default: Maprang)
- อีเมล: ตั้ง `SMTP_*` ใน `.env` — ถ้ายังไม่ตั้ง ปุ่มส่งจะคืนพรีวิวจำนวนรายการแทน (ไม่ error)
- ปรับเกณฑ์ "ใกล้หมด" ด้วย `EXPIRING_THRESHOLD_DAYS` (default 60 วัน)
