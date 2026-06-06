"""
session_store.py — Session himoya va holat boshqaruvi.

Funksiyalar:
    get_session_key()     — session_data dan unikal kalit
    mark_flood()          — flood qayd qilish (3x/kun → 1 kun blok)
    mark_banned()         — doimiy ban
    is_blocked()          — hozir bloklanganmi?
    release_expired_floods() — muddati o'tgan floodlarni ochish
    is_done_today()       — deduplikatsiya tekshiruvi
    mark_done_today()     — deduplikatsiya yozuvi
    get_stats()           — admin statistika
"""

from datetime import datetime, timezone, timedelta, date
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import TelegramSession, PostSessionLog

# ─── CONFIG ───────────────────────────────────────────────────────────────────
FLOOD_DAY_LIMIT  = 3  # 1 kunda shuncha marta flood bo'lsa → kunlik blok
FLOOD_BLOCK_DAYS = 1  # kunlik blok muddati


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


def get_session_key(session_data: dict) -> str | None:
    """Session uchun unikal identifikator: uid > user_id > number."""
    return (
        session_data.get("uid") or
        session_data.get("user_id") or
        session_data.get("number")
    )


def _find_session(db, session_data: dict):
    """TelegramSession ni DB dan topadi (uid → user_id → phone_num)."""
    uid = session_data.get("uid")
    if uid:
        row = db.query(TelegramSession).filter_by(uid=uid).first()
        if row:
            return row

    user_id = session_data.get("user_id")
    if user_id:
        row = db.query(TelegramSession).filter_by(user_id=user_id).first()
        if row:
            return row

    number = session_data.get("number")
    if number:
        return db.query(TelegramSession).filter_by(phone_num=number).first()

    return None


# ─── FLOOD ────────────────────────────────────────────────────────────────────

def mark_flood(session_data: dict, wait_sec: int) -> None:
    """
    Flood qayd qiladi.
    - 1 kunda 1-2 marta: faqat FloodWait muddati + 1 soat to'xtatish
    - 1 kunda 3+ marta: 1 kunlik to'liq blok
    """
    today = _today()
    with SessionLocal() as db:
        row = _find_session(db, session_data)
        if not row:
            return

        # Bugungi hisobni yangilash (kun o'tgan bo'lsa — noldan)
        if row.flood_date != today:
            row.flood_count_today = 0
            row.flood_date = today

        row.flood_count_today = (row.flood_count_today or 0) + 1

        if row.flood_count_today >= FLOOD_DAY_LIMIT:
            until = datetime.now(timezone.utc) + timedelta(days=FLOOD_BLOCK_DAYS)
        else:
            until = datetime.now(timezone.utc) + timedelta(seconds=wait_sec + 3600)

        row.is_working  = "flood"
        row.flood_until = until.isoformat()
        db.commit()


def mark_banned(session_data: dict) -> None:
    """Akkauntni doimiy ban belgilaydi (AuthKey, UserDeactivated...)."""
    with SessionLocal() as db:
        row = _find_session(db, session_data)
        if row:
            row.is_working = "banned"
            db.commit()


def is_blocked(session_data: dict) -> bool:
    """
    Session hozir bloklanganmi?
    Muddati o'tgan flood bloklar avtomatik 'active' ga o'tkaziladi.
    """
    now = _now()
    with SessionLocal() as db:
        row = _find_session(db, session_data)
        if not row:
            return False  # DB da yo'q — ruxsat

        if row.is_working == "banned":
            return True

        if row.is_working == "flood":
            if row.flood_until and row.flood_until > now:
                return True
            # Muddati o'tgan — ochamiz
            row.is_working = "active"
            row.flood_until = None
            db.commit()
            return False

    return False


def release_expired_floods() -> int:
    """Muddati o'tgan flood bloklar ni 'active' ga o'tkazadi. Periodically chaqiriladi."""
    now = _now()
    with SessionLocal() as db:
        rows = (
            db.query(TelegramSession)
            .filter(
                TelegramSession.is_working == "flood",
                TelegramSession.flood_until != None,
                TelegramSession.flood_until <= now,
            )
            .all()
        )
        for r in rows:
            r.is_working = "active"
            r.flood_until = None
        db.commit()
        return len(rows)


# ─── DEDUPLIKATSIYA ───────────────────────────────────────────────────────────

def is_done_today(session_key: str, post_link: str, service: str) -> bool:
    """Bu session bugun bu postga shu servisni yuborib bo'lganmi?"""
    today = _today()
    with SessionLocal() as db:
        return (
            db.query(PostSessionLog)
            .filter_by(
                session_uid=session_key,
                post_link=post_link,
                service=service,
                done_date=today,
            )
            .first() is not None
        )


def is_done_ever(session_key: str, post_link: str, service: str) -> bool:
    """Bu session bu postga shu servisni umuman yuborib bo'lganmi? (sana muhim emas)"""
    with SessionLocal() as db:
        return (
            db.query(PostSessionLog)
            .filter_by(
                session_uid=session_key,
                post_link=post_link,
                service=service,
            )
            .first() is not None
        )


def claim_done_today(session_key: str, post_link: str, service: str) -> bool:
    """Yuborilganini qayd qiladi (duplicate bo'lsa — indsilently skip)."""
    today = _today()
    with SessionLocal() as db:
        db.add(PostSessionLog(
            session_uid=session_key,
            post_link=post_link,
            service=service,
            done_date=today,
        ))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


# ─── STATISTIKA ───────────────────────────────────────────────────────────────

def mark_done_today(session_key: str, post_link: str, service: str) -> None:
    """Yuborilganini qayd qiladi (duplicate bo'lsa silently skip)."""
    claim_done_today(session_key, post_link, service)


def claim_sponsored_keyword_today(session_key: str, keyword: str) -> bool:
    """Bitta session uchun keyword qidiruvini bugunga atomik band qiladi."""
    normalized_keyword = normalize_sponsored_keyword(keyword)
    return claim_done_today(session_key, normalized_keyword, "sponsored_search")


def is_sponsored_keyword_done_today(session_key: str, keyword: str) -> bool:
    """Session bu keywordni bugun qidirgan bo'lsa True qaytaradi."""
    normalized_keyword = normalize_sponsored_keyword(keyword)
    return is_done_today(session_key, normalized_keyword, "sponsored_search")


def normalize_sponsored_keyword(keyword: str) -> str:
    return " ".join(keyword.split()).casefold()


def get_active_sessions(limit: int | None = None) -> list[dict]:
    """Tasklarda ishlatiladigan active Telegram sessionlarni DB dan qaytaradi."""
    with SessionLocal() as db:
        query = (
            db.query(TelegramSession)
            .filter(TelegramSession.is_working == "active")
            .order_by(TelegramSession.id)
        )
        if limit:
            query = query.limit(limit)
        rows = query.all()

    sessions: list[dict] = []
    for row in rows:
        try:
            sessions.append({
                "uid":      row.uid,
                "session":  row.hash_session,
                "app_id":   int(row.api_id),
                "app_hash": row.api_hash,
                "proxy":    None,
                "number":   row.phone_num,
                "user_id":  row.user_id,
                "device":   row.phone_model,
            })
        except Exception:
            continue
    return sessions


def count_active_sessions() -> int:
    with SessionLocal() as db:
        return db.query(TelegramSession).filter(TelegramSession.is_working == "active").count()


def get_stats() -> dict:
    """Admin uchun session holat statistikasi."""
    now = _now()
    with SessionLocal() as db:
        all_rows = db.query(TelegramSession).all()

    total = active = flood = banned = sleep = 0
    flood_details: list[dict] = []

    for r in all_rows:
        total += 1
        if r.is_working == "banned":
            banned += 1
        elif r.is_working == "flood" and r.flood_until and r.flood_until > now:
            flood += 1
            flood_details.append({
                "uid":         r.uid,
                "phone_num":   r.phone_num,
                "flood_until": r.flood_until,
                "flood_count": r.flood_count_today or 0,
            })
        elif r.is_working == "active":
            active += 1
        else:
            sleep += 1

    return {
        "total":          total,
        "active":         active,
        "flood":          flood,
        "banned":         banned,
        "sleep":          sleep,
        "flood_details":  flood_details,
    }
