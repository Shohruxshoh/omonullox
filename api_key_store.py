"""
api_key_store.py — SQLAlchemy ORM orqali API kalitlarni boshqaradi.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from database import SessionLocal
from models import ApiKey, User

DEFAULT_PRIORITY = 1000


def _db() -> Session:
    return SessionLocal()


def init_db() -> None:
    """Tarixiy moslik — haqiqiy migratsiya: `alembic upgrade head`"""
    pass


def register(api_key: str, priority: int = DEFAULT_PRIORITY, user_id: int | None = None) -> None:
    with _db() as db:
        existing = db.query(ApiKey).filter(ApiKey.api_key == api_key).first()
        if existing:
            existing.priority = priority
            existing.user_id = user_id
        else:
            db.add(ApiKey(
                api_key=api_key,
                user_id=user_id,
                priority=priority,
                created_at=datetime.now(timezone.utc).isoformat(),
            ))
        db.commit()


def ensure_for_user(user_id: int, priority: int = DEFAULT_PRIORITY) -> dict:
    """User uchun bitta API key borligini kafolatlaydi."""
    with _db() as db:
        existing = db.query(ApiKey).filter(ApiKey.user_id == user_id).first()
        if existing:
            return _to_dict(existing)

        user = db.get(User, user_id)
        if not user:
            raise ValueError(f"User #{user_id} topilmadi")

        row = ApiKey(
            api_key=str(uuid.uuid4()),
            user_id=user_id,
            priority=priority,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _to_dict(row)


def get_for_user(user_id: int) -> dict | None:
    with _db() as db:
        row = db.query(ApiKey).filter(ApiKey.user_id == user_id).first()
        return _to_dict(row) if row else None


def get_priority(api_key: str) -> int | None:
    with _db() as db:
        row = db.query(ApiKey).filter(ApiKey.api_key == api_key).first()
        return row.priority if row else None


def get_priority_for_user(api_key: str, user_id: int) -> int | None:
    with _db() as db:
        row = (
            db.query(ApiKey)
            .filter(ApiKey.api_key == api_key, ApiKey.user_id == user_id)
            .first()
        )
        return row.priority if row else None


def set_priority(api_key: str, priority: int) -> bool:
    with _db() as db:
        row = db.query(ApiKey).filter(ApiKey.api_key == api_key).first()
        if not row:
            return False
        row.priority = priority
        db.commit()
        return True


def delete_key(api_key: str) -> bool:
    with _db() as db:
        deleted = db.query(ApiKey).filter(ApiKey.api_key == api_key).delete()
        db.commit()
        return deleted > 0


def key_exists(api_key: str) -> bool:
    with _db() as db:
        return db.query(ApiKey).filter(ApiKey.api_key == api_key).first() is not None


def list_all() -> list[dict]:
    with _db() as db:
        rows = db.query(ApiKey).order_by(ApiKey.priority, ApiKey.created_at).all()
        return [_to_dict(r) for r in rows]


def _to_dict(row: ApiKey) -> dict:
    return {
        "api_key": row.api_key,
        "user_id": row.user_id,
        "username": row.user.username if row.user else None,
        "priority": row.priority,
        "created_at": row.created_at,
    }
