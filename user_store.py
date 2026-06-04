"""
user_store.py — SQLAlchemy ORM orqali User va Session boshqaruvi.
"""

import hashlib
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database import SessionLocal
from models import User, UserSession

ROLE_ADMIN = "admin"
ROLE_USER  = "user"


# ─── PAROL ────────────────────────────────────────────────────────────────────
def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
        return dk.hex() == dk_hex
    except Exception:
        return False


def _db() -> Session:
    return SessionLocal()


# ─── USER CRUD ────────────────────────────────────────────────────────────────
def create_user(username: str, password: str, role: str = ROLE_USER) -> dict:
    if role not in (ROLE_ADMIN, ROLE_USER):
        raise ValueError(f"Noto'g'ri rol: {role}")

    with _db() as db:
        if db.query(User).filter(User.username == username).first():
            raise ValueError(f"'{username}' allaqachon mavjud")

        user = User(
            username=username,
            password_hash=_hash_password(password),
            role=role,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"id": user.id, "username": user.username, "role": user.role}


def get_user_by_username(username: str) -> dict | None:
    with _db() as db:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return None
        return {
            "id": user.id,
            "username": user.username,
            "password_hash": user.password_hash,
            "role": user.role,
        }


def list_users() -> list[dict]:
    with _db() as db:
        rows = db.query(User).order_by(User.id).all()
        return [
            {"id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at}
            for u in rows
        ]


def delete_user(user_id: int) -> bool:
    with _db() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False
        db.delete(user)
        db.commit()
        return True


def set_password(username: str, password: str) -> bool:
    with _db() as db:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return False
        user.password_hash = _hash_password(password)
        db.commit()
        return True


def admin_exists() -> bool:
    with _db() as db:
        return db.query(User).filter(User.role == ROLE_ADMIN).first() is not None


# ─── SESSION (TOKEN) ──────────────────────────────────────────────────────────
def create_token(user_id: int) -> str:
    token = str(uuid.uuid4())
    with _db() as db:
        db.add(UserSession(
            token=token,
            user_id=user_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        ))
        db.commit()
    return token


def get_user_by_token(token: str) -> dict | None:
    if not token:
        return None
    with _db() as db:
        sess = (
            db.query(UserSession)
            .filter(UserSession.token == token)
            .first()
        )
        if not sess:
            return None
        u = sess.user
        return {"id": u.id, "username": u.username, "role": u.role}


def delete_token(token: str) -> None:
    with _db() as db:
        db.query(UserSession).filter(UserSession.token == token).delete()
        db.commit()


def login(username: str, password: str) -> str | None:
    user = get_user_by_username(username)
    if not user or not _verify_password(password, user["password_hash"]):
        return None
    return create_token(user["id"])


# ─── COMPAT: init_db (Alembic ishlatilganda bu shart emas) ────────────────────
def init_db() -> None:
    """
    Tarixiy moslik uchun qoldirilgan.
    Haqiqiy migratsiya: `alembic upgrade head`
    """
    pass
