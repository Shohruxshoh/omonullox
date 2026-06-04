"""
settings_store.py — DB da saqlanadigan global sozlamalar.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from database import SessionLocal
from models import SystemSetting

PARALLEL_ACCOUNTS_KEY = "parallel_accounts"
MIN_PARALLEL_ACCOUNTS = 1
MAX_PARALLEL_ACCOUNTS = 5000


def _db() -> Session:
    return SessionLocal()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_defaults(default_parallel_accounts: int) -> None:
    set_default_int(PARALLEL_ACCOUNTS_KEY, default_parallel_accounts)


def set_default_int(key: str, value: int) -> None:
    with _db() as db:
        row = db.get(SystemSetting, key)
        if not row:
            db.add(SystemSetting(key=key, value=str(value), updated_at=_now()))
            db.commit()


def get_int(key: str, default: int) -> int:
    with _db() as db:
        row = db.get(SystemSetting, key)
        if not row:
            return default
        try:
            return int(row.value)
        except (TypeError, ValueError):
            return default


def set_int(key: str, value: int) -> int:
    with _db() as db:
        row = db.get(SystemSetting, key)
        if not row:
            row = SystemSetting(key=key, value=str(value), updated_at=_now())
            db.add(row)
        else:
            row.value = str(value)
            row.updated_at = _now()
        db.commit()
    return value


def get_parallel_accounts(default: int) -> int:
    value = get_int(PARALLEL_ACCOUNTS_KEY, default)
    return max(MIN_PARALLEL_ACCOUNTS, min(MAX_PARALLEL_ACCOUNTS, value))


def set_parallel_accounts(value: int) -> int:
    value = max(MIN_PARALLEL_ACCOUNTS, min(MAX_PARALLEL_ACCOUNTS, value))
    return set_int(PARALLEL_ACCOUNTS_KEY, value)
