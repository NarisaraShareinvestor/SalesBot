"""Database engine/session — SQLite by default, Postgres-ready via DATABASE_URL."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

load_dotenv()

# Default: SQLite file next to the backend package
_DEFAULT_SQLITE = f"sqlite:///{Path(__file__).resolve().parent / 'salesbot.db'}"
DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_SQLITE)

_is_pg = "postgresql" in DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={} if _is_pg else {"check_same_thread": False},
    poolclass=None if _is_pg else StaticPool,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
