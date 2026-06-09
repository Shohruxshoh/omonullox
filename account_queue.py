"""
account_queue.py — Global per-account lock registry.

MAQSAD: Hech qachon bitta akkaunt parallel (bir vaqtda) ishlamasligi uchun.

Har bir akkauntga bitta asyncio.Lock() beriladi.
Lock band bo'lsa — boshqa task kutadi (hech qachon o'tkazib yuborilmaydi).

KAFOLAT:
    asyncio.Lock() — Python asyncio darajasida kafolatlanadi.
    Bir vaqtda faqat BITTA coroutine lock ichida bo'lishi mumkin.
    Bu fizikaviy imkonsiz bitta akkaunt parallel ishlay olishi degani.
"""

import asyncio
import hashlib

# ─── GLOBAL LOCK REGISTRY ────────────────────────────────────────────────────
# key   = account_id (telefon raqam yoki sessiya identifikatori)
# value = asyncio.Lock()
_account_locks: dict[str, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()   # _account_locks dict ni thread-safe yangilash uchun


def _get_account_id(session: dict) -> str:
    """
    Akkaunt uchun noyob identifikator qaytaradi.
    uid/user_id/telefon mavjud bo'lsa ishlatadi.
    Yo'q bo'lsa to'liq session string hashidan foydalanadi.
    """
    account_id = (
        session.get("uid") or
        session.get("user_id") or
        session.get("number") or
        session.get("phone")
    )
    if account_id:
        return str(account_id).strip()

    session_value = str(session.get("session") or "")
    if session_value:
        return hashlib.sha256(session_value.encode()).hexdigest()
    return "unknown"


async def get_lock(session: dict) -> asyncio.Lock:
    """
    Berilgan akkaunt uchun asyncio.Lock() qaytaradi.
    Agar lock hali yaratilmagan bo'lsa — yangisini yaratadi.
    Thread-safe (asyncio.Lock() yordamida himoyalangan).
    """
    account_id = _get_account_id(session)

    # Agar lock allaqachon mavjud — to'g'ri qaytaramiz (lock olmay)
    if account_id in _account_locks:
        return _account_locks[account_id]

    # Yangi lock yaratish uchun registry ni himoya qilamiz
    async with _registry_lock:
        # Double-check: boshqa coroutine yaratib qo'ygan bo'lishi mumkin
        if account_id not in _account_locks:
            _account_locks[account_id] = asyncio.Lock()
        return _account_locks[account_id]


async def try_acquire_idle_lock(session: dict) -> asyncio.Lock | None:
    """
    Session ayni paytda bo'sh bo'lsa lockni darhol oladi.
    Band sessionni kutmaydi va None qaytaradi.
    """
    lock = await get_lock(session)
    waiters = getattr(lock, "_waiters", None)
    if lock.locked() or any(not waiter.cancelled() for waiter in (waiters or ())):
        return None

    # A free asyncio lock is acquired without yielding to another coroutine.
    await lock.acquire()
    return lock


def get_stats() -> dict:
    """
    Monitoring: nechta akkaunt hozir band / bo'sh.
    """
    total = len(_account_locks)
    busy = sum(1 for lock in _account_locks.values() if lock.locked())
    return {
        "total_accounts": total,
        "busy": busy,
        "free": total - busy,
    }
