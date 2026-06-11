# SalesBot — FastAPI + SQLite, single container
FROM python:3.11-slim

WORKDIR /app

# deps first (better layer caching)
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# app code (salesbot.db / *.json / .env are gitignored → not copied)
COPY backend/ backend/
COPY frontend/ frontend/

# persistent SQLite lives on a mounted volume at /data
ENV DATABASE_URL=sqlite:////data/salesbot.db

WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
