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

# ─── GLOBAL LOCK REGISTRY ────────────────────────────────────────────────────
# key   = account_id (telefon raqam yoki sessiya identifikatori)
# value = asyncio.Lock()
_account_locks: dict[str, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()   # _account_locks dict ni thread-safe yangilash uchun


def _get_account_id(session: dict) -> str:
    """
    Akkaunt uchun noyob identifikator qaytaradi.
    'number' maydoni bo'lsa — ishlatadi (eng ishonchli).
    Yo'q bo'lsa — session string ning birinchi 32 belgi.
    """
    number = session.get("number") or session.get("phone")
    if number:
        return str(number).strip()
    # fallback: session string identifikatori
    return str(session.get("session", "unknown"))[:32]


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
