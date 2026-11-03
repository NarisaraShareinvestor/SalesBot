# SalesBot — IR Sales Intelligence Assistant

ผู้ช่วย AI สำหรับทีม IR Sales ของ ShareInvestor — แชทถามข้อมูลลูกค้า/สัญญาแบบภาษาธรรมชาติ,
ดู/เพิ่ม/แก้ข้อมูล, และแจ้งเตือนสัญญาใกล้หมดอายุ ฉลาดด้วย **function-calling** บนฐานข้อมูลจริง

หน้าตา Warm Glass (ชุดเดียวกับ AI Monitor) อยู่ในไฟล์เดียว `frontend/static/glass.css` — ลบบรรทัด `<link>` ก็กลับเป็นแบบเดิม
Live: https://salesbot.ohmai.me (VPS 187.127.218.42 — ดู `deploy/DEPLOY.md`)

## สถานะลูกค้า (stage) — นิยามเดียวทั้งระบบ
`services.lifecycle()` เป็นที่เดียวที่ตัดสินสถานะ — Dashboard, ตาราง Data, แจ้งเตือน, เรดาร์ และทุก tool ของแชทใช้ตัวนี้
ตัวเลขจึงตรงกันทุกที่ (มีเทสต์ `tests/test_consistency.py` กันไว้)

| stage | ความหมาย |
|---|---|
| active | เหลือมากกว่า `EXPIRING_THRESHOLD_DAYS` (60) วัน |
| expiring_soon | เหลือ 0–60 วัน |
| expired | หมดอายุแล้ว ยังไม่ต่อ |
| cancelled | `contract_status` = Cancelled (ไม่ว่าวันไหน) |
| pending | ไม่มีวันหมด + On Process / Payment Pending |
| unknown | ไม่มีวันหมดอายุ |

`status_stale` = คอลัมน์ status ยังเขียน Active แต่วันหมดผ่านไปแล้ว → ต้องอัปเดตหรือตามต่อสัญญา

## ฟีเจอร์
1. **Dashboard** (ตอบคำถามผู้จัดการ) — มูลค่าสัญญาในมือ, มูลค่าที่ครบกำหนดใน 0–30/31–60/61–90 วัน,
   หลุดใน 90 วันที่ผ่านมา, สถานะที่ต้องอัปเดต, งานเลยกำหนด · รายการ "ต้องโทรก่อน" พร้อมระดับเสี่ยง ·
   ภาระงานตาม AE (คลิก → ดูลูกค้าของคนนั้น) · สรุปเช้านี้ (LLM, cache วันละครั้ง)
2. **Chat** — tool-calling บน DB จริง (ไม่เดา/ไม่นับเอง)
   - อ่าน: `search_customers` (fuzzy ไทย), `get_customer` (รวมงานค้าง + ความจำทีม), `list_customers` (AE/stage/สถานะค้าง),
     `list_expiring`, `aggregate` (stage/AE/อุตสาหกรรม/เกรด), `portfolio_overview` (ตัวเลขชุดเดียวกับ Dashboard),
     `sales_opportunities` (retain/winback/upsell), `list_team`
   - เขียน: `add_followup`, `manage_followup`, `remember_about_customer`
   - คำตอบลิงก์รหัสลูกค้า `[ACE](customer:ACE)` → คลิกเปิดหน้าลูกค้า
3. **Data** — ตารางลูกค้าเรียงตามความด่วน (ใกล้หมด → เพิ่งหลุด → ใช้งานอยู่ → อื่น ๆ), กรองตาม stage, Export CSV,
   ฟอร์มแก้ไข + งานติดตาม + ความจำทีมต่อลูกค้า
4. **เรดาร์โอกาสขาย** — เสี่ยงหลุด / ดึงกลับ / อัปเซล พร้อมร่างข้อความ
5. **Notifications** — งานติดตาม + สัญญาใกล้หมด, ตัวกรอง, ปฏิทิน

## Login
ทุก `/api/*` ต้องมี session cookie ที่เซ็นแล้ว (`backend/auth.py`, HttpOnly, 30 วัน) — ตัวตนในแชทมาจาก session ปลอมไม่ได้
- ตั้ง `AZURE_*` แล้ว → ปุ่ม Microsoft Teams อย่างเดียว
- ยังไม่ตั้ง → อีเมล @shareinvestor.com + ชื่อ + `TEAM_PASSWORD` (ผิดเกิน 8 ครั้ง/5 นาที ถูกพัก)

## โครงสร้าง
```
backend/
  main.py            FastAPI: routes, middleware login, chat loop, briefing
  auth.py            session cookie + team password
  services.py        lifecycle() / stage + serialize ลูกค้า + dashboard_data
  radar.py           retain / winback / upsell / renewal_outlook (Dashboard)
  chat_tools.py      นิยาม tool + executor
  notifications.py   ใกล้หมด + ส่งอีเมลสรุป
  memory_manager.py  ความจำผู้ใช้ / ความจำทีมต่อลูกค้า
  improve.py         log ปัญหาแชท + บทเรียน (หน้า Admin)
  microsoft_teams.py OAuth + ปฏิทิน Teams
  import_data.py     Excel → salesbot.db (upsert)
frontend/index.html  SPA · frontend/static/ glass.css + bg.jpg
tests/               ความสอดคล้องของตัวเลข + login
```

## รันในเครื่อง
```bash
cp .env.example .env        # ใส่ OPENAI_API_KEY, TEAM_PASSWORD
./run.sh                    # setup venv + seed DB + เปิด http://localhost:8000
```

## เทสต์ (ใช้สำเนา DB เสมอ — แชทเขียนความจำ/งานลง DB)
```bash
cp backend/salesbot.db /tmp/sb-test.db
DATABASE_URL=sqlite:////tmp/sb-test.db .venv/bin/python -m pytest tests -q
```

## ข้อมูล
Import จาก `Data/` (ไม่ขึ้น git) — `IR-Agreement-Status 2025-2.xlsx` (สัญญา, 1 แถว/account เลือกที่หมดล่าสุด)
+ `20260512-PR-All-IR-List.xlsx` (ผู้ติดต่อ IR) · รัน `import_data.py` ซ้ำได้ (upsert)

## หมายเหตุ
- อีเมล: ตั้ง `SMTP_*` — ถ้ายังไม่ตั้ง ปุ่มส่งจะคืนพรีวิวแทน
- เกณฑ์ "ใกล้หมด": `EXPIRING_THRESHOLD_DAYS` (default 60)
- SQLite เปิด WAL + connection ต่อ thread (อย่ากลับไปใช้ StaticPool — query ชนกันจน "malformed")
