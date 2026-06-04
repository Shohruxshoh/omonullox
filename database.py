"""
database.py — SQLAlchemy engine, Base va session factory.
PostgreSQL (psycopg2) ishlatadi. DATABASE_URL muhit o'zgaruvchisidan olinadi.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postapi:secret123@localhost:5432/postapi",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # ulanish uzilganini avtomatik aniqlaydi
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI Depends uchun DB session generator."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
