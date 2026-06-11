#!/usr/bin/env bash
# SalesBot — setup & run
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r backend/requirements.txt
fi

# seed DB ครั้งแรก (idempotent — รันซ้ำได้)
if [ ! -f backend/salesbot.db ]; then
  (cd backend && ../.venv/bin/python import_data.py)
fi

cd backend
exec ../.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
