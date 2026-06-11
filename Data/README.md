# Data

ไฟล์ข้อมูลลูกค้าจริง **ไม่ถูกเก็บใน GitHub** (อยู่ใน `.gitignore`) เพราะมีอีเมลผู้ติดต่อและมูลค่าสัญญา

วางไฟล์ต่อไปนี้ในโฟลเดอร์นี้ก่อนรัน `python backend/import_data.py`:

- `IR-Agreement-Status 2025-2.xlsx` — สัญญาหลัก (sheet "Company visit")
- `20260512-PR-All-IR-List.xlsx` — รายชื่อผู้ติดต่อ IR (join ด้วย Account)
