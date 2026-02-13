from __future__ import annotations

from typing import Generator
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не задан! Добавь его в Environment Variables на Render")

# Фикс для IPv6 проблемы Render → Supabase
if DATABASE_URL.startswith("postgresql://"):
    # Принудительно используем IPv4
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://")
    
    # Парсим хост из DATABASE_URL
    if "supabase.co" in DATABASE_URL:
        # Добавляем параметры подключения для принудительного IPv4
        if "?" in DATABASE_URL:
            DATABASE_URL += "&hostaddr=::ffff:"
        else:
            DATABASE_URL += "?hostaddr=::ffff:"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={
        "connect_timeout": 30,
        "sslmode": "require",
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
