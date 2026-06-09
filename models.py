"""
models.py — SQLAlchemy ORM modellari.

Jadvallar:
    users              — foydalanuvchilar (admin / user roli)
    sessions           — login tokenlari
    api_keys           — API kalitlar va ularning prioritetlari
    task_logs          — har bir task tarixi (key, servis, foiz, vaqt)
    telegram_sessions  — Telegram akkauntlar (session string, phone, status...)
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Float, SmallInteger, UniqueConstraint
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    role          = Column(String(20), nullable=False, default="user")
    created_at    = Column(String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User id={self.id} username={self.username} role={self.role}>"


class UserSession(Base):
    __tablename__ = "sessions"

    token      = Column(String(36), primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    user = relationship("User", back_populates="sessions")

    def __repr__(self):
        return f"<UserSession token={self.token[:8]}... user_id={self.user_id}>"


class ApiKey(Base):
    __tablename__ = "api_keys"

    api_key    = Column(String(36), primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, unique=True, index=True)
    priority   = Column(Integer, nullable=False, default=1000)
    created_at = Column(String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    user = relationship("User", back_populates="api_keys")

    def __repr__(self):
        return f"<ApiKey key={self.api_key[:8]}... user_id={self.user_id} priority={self.priority}>"


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key        = Column(String(100), primary_key=True)
    value      = Column(Text, nullable=False)
    updated_at = Column(String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())

    def __repr__(self):
        return f"<SystemSetting key={self.key}>"


class TaskLog(Base):
    """Har bir task haqida to'liq ma'lumot."""
    __tablename__ = "task_logs"

    task_id     = Column(String(36), primary_key=True)
    api_key     = Column(String(36), nullable=False, index=True)
    service     = Column(String(20), nullable=False)
    post_link   = Column(Text,       nullable=False)
    priority    = Column(Integer,    nullable=False, default=1000)
    status      = Column(String(20), nullable=False, default="queued")
    total       = Column(Integer,    nullable=False, default=0)
    done        = Column(Integer,    nullable=False, default=0)
    error       = Column(Text,       nullable=True)
    task_meta   = Column(Text,       nullable=True)   # JSON: {"accounts": N, "emoji": "👍"}
    created_at  = Column(String(50), nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())
    started_at  = Column(String(50), nullable=True)
    finished_at = Column(String(50), nullable=True)

    def __repr__(self):
        return f"<TaskLog id={self.task_id} service={self.service} status={self.status}>"


class TelegramSession(Base):
    """
    Telegram akkauntlar jadvali.

    Ustunlar:
        id               — auto PK
        uid              — unikal identifikator (string, masalan '6615b83e6f66d')
        api_id           — Telegram app_id
        api_hash         — Telegram app_hash
        hash_session     — Telethon StringSession
        phone_model      — qurilma modeli
        is_premium       — premium akkaunt? ('0' / '1')
        username         — @username (NULL bo'lishi mumkin)
        user_id          — Telegram user ID
        phone_num        — telefon raqam
        app_version      — ilova versiyasi
        is_scam          — scam hisoblanganmi? ('yes' / 'no')
        app_name         — ilova nomi
        lang_code        — til kodi ('uz', 'ru', ...)
        is_working       — holat: 'active' / 'sleep' / 'banned' / 'flood'
        pid_id           — tashqi tizim ID (optional)
        date_last_online — oxirgi faollik vaqti (ISO string)
    """
    __tablename__ = "telegram_sessions"

    id               = Column(Integer,     primary_key=True, autoincrement=True)
    uid              = Column(String(64),  unique=True, nullable=False, index=True)
    api_id           = Column(String(20),  nullable=False)
    api_hash         = Column(String(64),  nullable=False)
    hash_session     = Column(Text,        nullable=False)
    phone_model      = Column(String(128), nullable=True)
    is_premium       = Column(String(4),   nullable=False, default="0")   # '0' / '1'
    username         = Column(String(64),  nullable=True)
    user_id          = Column(String(32),  nullable=True, index=True)
    phone_num        = Column(String(32),  nullable=True, index=True)
    app_version      = Column(String(32),  nullable=True)
    is_scam          = Column(String(8),   nullable=False, default="no")   # 'yes' / 'no'
    app_name         = Column(String(64),  nullable=True)
    lang_code        = Column(String(16),  nullable=True)
    is_working       = Column(String(32),  nullable=False, default="sleep")  # 'active'/'sleep'/'banned'/'flood'
    flood_until      = Column(String(50),  nullable=True)   # ISO datetime — qachon ochiladi
    flood_count_today= Column(Integer,     nullable=False, default=0)  # bugungi flood soni
    flood_date       = Column(String(10),  nullable=True)   # YYYY-MM-DD
    pid_id           = Column(String(32),  nullable=True)
    date_last_online = Column(String(50),  nullable=True)
    last_used_at     = Column(String(50),  nullable=True, index=True)

    def __repr__(self):
        return f"<TelegramSession id={self.id} phone={self.phone_num} status={self.is_working}>"


class PostSessionLog(Base):
    """
    Deduplikatsiya jadvali.
    Har bir (session_uid, post_link, service, done_date) kombinatsiyasi 1 marta.
    """
    __tablename__ = "post_session_logs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    session_uid = Column(String(64), nullable=False, index=True)  # uid / user_id / number
    post_link   = Column(Text,       nullable=False)
    service     = Column(String(20), nullable=False)
    done_date   = Column(String(10), nullable=False)              # YYYY-MM-DD

    __table_args__ = (
        UniqueConstraint(
            "session_uid", "post_link", "service", "done_date",
            name="uq_session_post_day"
        ),
    )

    def __repr__(self):
        return f"<PostSessionLog {self.session_uid} {self.service} {self.done_date}>"
