"""
api_server.py — Priority Queue + User Auth (admin/user rol) asosida FastAPI server.

Auth tizimi:
    POST /auth/register  — ro'yxatdan o'tish
    POST /auth/login     — login → token qaytaradi
    POST /auth/logout    — token o'chirish

    Token X-Token headerida yuboriladi.
    Admin endpointlari uchun role="admin" bo'lishi shart.

Task endpointlari:
    POST /task/views      — views vazifasi yuborish (X-API-Key header)
    POST /task/reactions  — reactions vazifasi yuborish (X-API-Key header)
    POST /task/shares     — shares vazifasi yuborish (X-API-Key header)
    GET  /status/{id}     — task holati
    GET  /tasks          — barcha tasklar
    GET  /queue          — navbat uzunligi
    GET  /locks          — akkaunt band holati

Admin endpointlari (token + admin roli kerak):
    POST   /admin/keys          — yangi API key (UUID auto)
    GET    /admin/keys          — barcha keylar
    PATCH  /admin/keys/{key}    — prioritet o'zgartirish
    DELETE /admin/keys/{key}    — kalit o'chirish
    GET    /admin/users         — barcha foydalanuvchilar
    DELETE /admin/users/{id}    — foydalanuvchini o'chirish
"""

import asyncio
import csv
import hashlib
import io
import json
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Query, UploadFile, File, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, Field
import redis.asyncio as aioredis
import uvicorn

from redis_main import RedisSessionManager
from post_services import (
    SERVICE_MAP,
    parse_post_link,
    check_reaction_available,
    send_reactions_to_post,
    find_sponsored_peers,
)
from account_queue import get_lock, get_stats
import api_key_store as key_store
import user_store as users
import task_log_store as tlog
import session_store
import settings_store
from database import SessionLocal
from models import TelegramSession
from queue_control import SponsoredPriorityController, pop_first_unlocked
from worker_guard import acquire_worker_lock, release_worker_lock

# ─── CONFIG ──────────────────────────────────────────────────────────────────
REDIS_KEY         = os.environ.get("REDIS_KEY", "telegram:sessions:full")
REDIS_URL         = os.environ.get("REDIS_URL", "redis://localhost:6379")
DEFAULT_PARALLEL_ACCOUNTS = int(os.environ.get("PARALLEL_ACCOUNTS", "400"))
SPONSORED_TARGET_STOP_THRESHOLD = max(
    1,
    int(os.environ.get("SPONSORED_TARGET_STOP_THRESHOLD", "8")),
)
SPONSORED_QUEUE_PRIORITY = 0
FRONTEND_DIR      = os.path.join(os.path.dirname(__file__), "frontend")
DEFAULT_SESSION_API_ID = 2040
DEFAULT_SESSION_API_HASH = "b18441a1ff607e10a989891a5462e627"

# ─── GLOBALS ─────────────────────────────────────────────────────────────────
task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
sponsored_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
sponsored_priority = SponsoredPriorityController()
task_status: dict[str, dict] = {}


def _queue_timestamp(created_at: str | None = None) -> float:
    if not created_at:
        return time.time()
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _get_real_ip(request: Request) -> str:
    """Nginx/Cloudflare ortida haqiqiy IP ni oladi."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host

limiter = Limiter(key_func=_get_real_ip)

# ─── CIRCUIT BREAKER ─────────────────────────────────────────────────────────
IS_PAUSED     = False        # tizim to'xtatilganmi?
PAUSED_REASON = ""           # sabab
_cb_errors: list[float] = [] # xato vaqtlari (timestamp)
CB_THRESHOLD  = int(os.environ.get("CB_THRESHOLD", "10"))  # 10 xato
CB_WINDOW     = int(os.environ.get("CB_WINDOW",    "300")) # 5 daqiqa


def _circuit_breaker_record(error_type: str) -> None:
    """Xatoni qayd qiladi. Chegaradan oshsa — tizimni to'xtatadi."""
    global IS_PAUSED, PAUSED_REASON, _cb_errors
    now = time.time()
    _cb_errors.append(now)
    _cb_errors = [t for t in _cb_errors if now - t < CB_WINDOW]
    if len(_cb_errors) >= CB_THRESHOLD and not IS_PAUSED:
        IS_PAUSED     = True
        PAUSED_REASON = (
            f"{len(_cb_errors)} ta '{error_type}' xato "
            f"{CB_WINDOW // 60} daqiqa ichida — tizim to'xtatildi."
        )
        print(f"[⛔ CIRCUIT BREAKER] {PAUSED_REASON}")


# ─── SCHEMAS ─────────────────────────────────────────────────────────────────
class TaskRequest(BaseModel):
    post_link: str
    accounts: Optional[int] = None


class ReactionTaskRequest(BaseModel):
    post_link: str
    reaction: str          # Yuborish kerak bo'lgan emoji, masalan: "👍"
    accounts: Optional[int] = None


class SponsoredSearchRequest(BaseModel):
    search_key: str = Field(min_length=1, max_length=100)
    channel_username: str = Field(min_length=1, max_length=100)
    accounts: int = Field(default=1, ge=1, le=1000)
    parallel_sessions: int = Field(default=5, ge=1, le=1000)


class TaskResponse(BaseModel):
    task_id:  str
    status:   str
    priority: int
    message:  str


class StatusResponse(BaseModel):
    task_id:      str
    api_key:      str
    status:       str
    service:      str
    post_link:    str
    priority:     int
    total:        int
    done:         int
    percent:      float
    skipped:      int = 0
    flooded:      int = 0
    banned_count: int = 0
    error:        Optional[str]
    created_at:   str
    started_at:   Optional[str]
    finished_at:  Optional[str]


class KeyPatchRequest(BaseModel):
    priority: int = Field(ge=1, le=10000)


class KeyCreateRequest(BaseModel):
    priority: int = Field(default=key_store.DEFAULT_PRIORITY, ge=1, le=10000)
    user_id: Optional[int] = None


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    role: Literal["admin", "user"] = "user"


class LoginRequest(BaseModel):
    username: str
    password: str


class RawSessionsRequest(BaseModel):
    sessions: str
    api_id: int = DEFAULT_SESSION_API_ID
    api_hash: str = DEFAULT_SESSION_API_HASH
    update: bool = True
    default_status: Literal["active", "sleep"] = "active"


class SystemSettingsRequest(BaseModel):
    parallel_accounts: int = Field(
        ge=settings_store.MIN_PARALLEL_ACCOUNTS,
        le=settings_store.MAX_PARALLEL_ACCOUNTS,
    )


# ─── AUTH DEPENDENCIES ───────────────────────────────────────────────────────
def get_current_user(x_token: Optional[str] = Header(default=None)) -> dict:
    """
    X-Token headeridan foydalanuvchini aniqlaydi.
    Token noto'g'ri bo'lsa — 401.
    """
    if not x_token:
        raise HTTPException(status_code=401, detail="X-Token header majburiy")
    user = users.get_user_by_token(x_token)
    if not user:
        raise HTTPException(status_code=401, detail="Token noto'g'ri yoki muddati o'tgan")
    return user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Faqat admin rolga ruxsat beradi."""
    if current_user["role"] != users.ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Bu amal faqat admin uchun")
    return current_user


def validate_api_key(x_api_key: Optional[str] = Header(default=None)) -> int:
    """X-API-Key headerini tekshiradi va prioritetni qaytaradi."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header majburiy")
    priority = key_store.get_priority(x_api_key)
    if priority is None:
        raise HTTPException(status_code=401, detail="Noma'lum API key")
    return priority


def validate_user_api_key(
    x_api_key: Optional[str] = Header(default=None),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Task endpointlari uchun: token useri faqat o'z API keyini ishlata oladi."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header majburiy")
    priority = key_store.get_priority_for_user(x_api_key, current_user["id"])
    if priority is None:
        raise HTTPException(status_code=401, detail="Bu API key ushbu userga tegishli emas")
    return {
        "api_key": x_api_key,
        "priority": priority,
        "user": current_user,
    }


# ─── BACKGROUND TASKS ────────────────────────────────────────────────────────
async def flood_release_loop():
    """Har 1 daqiqada muddati o'tgan flood bloklar ni 'active' ga o'tkazadi."""
    while True:
        await asyncio.sleep(60)
        released = session_store.release_expired_floods()
        if released:
            print(f"[FLOOD] {released} ta session flood blokdan chiqarildi")


# ─── WORKER LOOP ─────────────────────────────────────────────────────────────
async def sponsored_worker_loop():
    """Sponsored qidiruvlarni FIFO bajaradi va oddiy tasklardan ustun qo'yadi."""
    while True:
        while IS_PAUSED:
            await asyncio.sleep(3)

        _, queued_at, job_id, job = await sponsored_queue.get()
        future: asyncio.Future = job["future"]
        try:
            task_status[job_id]["status"] = "running"
            tlog.set_running(job_id, job["req"].accounts)
            result = await _execute_sponsored_search(
                job["req"],
                job["search_key"],
                job["channel_username"],
                queue_waited_seconds=max(0, time.time() - queued_at),
                task_id=job_id,
            )
            result["task_id"] = job_id
            completed = int(result.get("checks_completed") or 0)
            total = max(completed, int(result.get("sessions_started") or 0))
            task_status[job_id]["status"] = "done"
            task_status[job_id]["total"] = total
            task_status[job_id]["done"] = completed
            task_status[job_id]["skipped"] = (
                int(result.get("daily_skipped") or 0)
                + int(result.get("blocked_skipped") or 0)
            )
            tlog.set_progress(job_id, total, completed)
            tlog.update_meta(job_id, {
                **job["log_meta"],
                "result": {
                    "found": result.get("found", 0),
                    "target_found_sessions": result.get("target_found_sessions", 0),
                    "sessions_started": result.get("sessions_started", 0),
                    "checks_completed": completed,
                    "daily_skipped": result.get("daily_skipped", 0),
                    "busy_waited": result.get("busy_waited", 0),
                    "views_sent": result.get("views_sent", 0),
                    "views_failed": result.get("views_failed", 0),
                    "stopped_early": result.get("stopped_early", False),
                    "queue_waited_seconds": result.get("queue_waited_seconds", 0),
                },
            })
            tlog.set_done(job_id)
            if not future.done():
                future.set_result(result)
        except Exception as e:
            task_status[job_id]["status"] = "error"
            task_status[job_id]["error"] = str(e)
            tlog.set_error(job_id, str(e))
            if not future.done():
                future.set_exception(e)
            print(f"[SPONSORED ERROR] [{job_id}] {e}")
        finally:
            sponsored_queue.task_done()
            await sponsored_priority.sponsored_finished(sponsored_queue)


async def worker_loop():
    while True:
        # ── Tizim to'xtatilgan bo'lsa navbatdagi taskni olmay kutamiz ──────────
        while IS_PAUSED:
            await asyncio.sleep(3)

        priority, ts, task_id, task = await sponsored_priority.get_normal_item(task_queue)

        service   = task["service"]
        post_link = task["post_link"]
        limit     = task.get("accounts")

        task_status[task_id]["status"] = "running"
        wait_sec = time.time() - ts
        print(
            f"[RUN] [{task_id}] priority={priority} | "
            f"{service.upper()} | {post_link} | waited={wait_sec:.1f}s"
        )

        try:
            channel, msg_id = parse_post_link(post_link)
            check_result = None   # reactions uchun checker natijasi

            sessions = session_store.get_active_sessions()
            if not sessions:
                err = "DB da active session topilmadi"
                task_status[task_id]["status"] = "error"
                task_status[task_id]["error"]  = err
                tlog.set_error(task_id, err)
                continue

            random.shuffle(sessions)
            if limit:
                sessions = sessions[:limit]

            # ── Reactions: checker account + emoji ───────────────────────────
            checker_done = False
            checker_key = None
            if service == "reactions":
                emoji = task.get("emoji", "👍")

                # Maksimal 3 ta checker sinab ko'ramiz
                MAX_CHECKERS = 3
                check_result = "skip"
                for _ in range(min(MAX_CHECKERS, len(sessions))):
                    checker = sessions.pop(0)
                    checker_key = session_store.get_session_key(checker)
                    checker_lock = await get_lock(checker)
                    async with sponsored_priority.normal_account_lock(checker_lock):
                        if checker_key and session_store.is_done_ever(checker_key, post_link, service):
                            check_result = "skip"
                            continue

                        check_result = await check_reaction_available(checker, channel, msg_id, emoji)
                        print(f"[CHECK] [{task_id}] checker natija: {check_result}")

                        if check_result == "ok" and checker_key:
                            session_store.mark_done_today(checker_key, post_link, service)
                        elif check_result in ("banned", "auth"):
                            session_store.mark_banned(checker)
                        elif check_result and check_result.startswith("flood:"):
                            try:
                                wait_s = int(check_result.split(":")[1])
                            except (IndexError, ValueError):
                                wait_s = 300
                            session_store.mark_flood(checker, wait_s)

                    # Aniq natija — davom etish yoki to'xtatish
                    if check_result in ("ok", "reaction_not_allowed"):
                        break
                    # flood yoki skip — keyingi checkerni sinab ko'ramiz

                if check_result == "reaction_not_allowed":
                    msg = f"'{emoji}' reaksiyasini yuborib bo'lmaydi — reaction yuborilmadi"
                    task_status[task_id]["status"] = "rejected"
                    task_status[task_id]["error"]  = msg
                    tlog.set_rejected(task_id, msg)
                    print(f"[REJECTED] [{task_id}] {msg}")
                    continue

                # Checker "ok" — o'zi yubordi, done ga qo'shamiz
                if check_result == "ok":
                    checker_done = True

                if check_result != "ok":
                    msg = f"'{emoji}' reaksiyasi checker orqali tasdiqlanmadi ({check_result}) - task boshlanmadi"
                    task_status[task_id]["status"] = "error"
                    task_status[task_id]["error"]  = msg
                    tlog.set_error(task_id, msg)
                    print(f"[ERROR] [{task_id}] {msg}")
                    continue

                # flood/skip bo'lsa — tekshirib bo'lmadi, baribir davom etamiz

                action_func = lambda s, c, m, _e=emoji: send_reactions_to_post(s, c, m, _e)
            else:
                action_func = SERVICE_MAP[service]

            # Reactions uchun checker ham total ga kiradi (u allaqachon yubordi)
            checker_bonus = 1 if checker_done else 0
            total = len(sessions) + checker_bonus
            task_status[task_id]["total"] = total
            tlog.set_running(task_id, total)
            if checker_done:
                task_status[task_id]["done"] += 1
                tlog.inc_done(task_id)
            parallel_accounts = settings_store.get_parallel_accounts(DEFAULT_PARALLEL_ACCOUNTS)
            task_status[task_id]["parallel_accounts"] = parallel_accounts
            semaphore = asyncio.Semaphore(parallel_accounts)

            async def run_one(session):
                # ── Tizim pauza bo'lsa — kutamiz (to'xtagan joydan davom) ────
                while IS_PAUSED:
                    await asyncio.sleep(2)

                # ── Session bloklangan? (flood / banned) ─────────────────────
                if session_store.is_blocked(session):
                    task_status[task_id]["skipped"] += 1
                    return

                # ── Deduplikatsiya ────────────────────────────────────────────
                # reactions: bir account bir postga umuman bir marta (abadiy)
                # boshqalar: faqat bugun tekshiriladi
                sess_key = session_store.get_session_key(session)
                if sess_key:
                    if service == "reactions":
                        if session_store.is_done_ever(sess_key, post_link, service):
                            task_status[task_id]["skipped"] += 1
                            return
                    elif session_store.is_done_today(sess_key, post_link, service):
                        task_status[task_id]["skipped"] += 1
                        return

                async with semaphore:
                    # ── Per-account lock ──────────────────────────────────────
                    account_lock = await get_lock(session)
                    async with sponsored_priority.normal_account_lock(account_lock):
                        await asyncio.sleep(random.uniform(0.1, 0.5))
                        result = await action_func(session, channel, msg_id)

                        if result == "ok":
                            task_status[task_id]["done"] += 1
                            tlog.inc_done(task_id)
                            if sess_key:
                                session_store.mark_done_today(sess_key, post_link, service)

                        elif result == "skip":
                            task_status[task_id]["skipped"] += 1

                        elif result and result.startswith("flood:"):
                            try:
                                wait_s = int(result.split(":")[1])
                            except (IndexError, ValueError):
                                wait_s = 300
                            session_store.mark_flood(session, wait_s)
                            task_status[task_id]["flooded"] += 1
                            _circuit_breaker_record("flood")

                        elif result in ("banned", "auth"):
                            session_store.mark_banned(session)
                            task_status[task_id]["banned_count"] += 1
                            _circuit_breaker_record(result)

            await asyncio.gather(*(run_one(s) for s in sessions))

            task_status[task_id]["status"] = "done"
            tlog.set_done(task_id)
            print(f"[DONE] [{task_id}] {service.upper()} | {total} akkaunt")

        except Exception as e:
            task_status[task_id]["status"] = "error"
            task_status[task_id]["error"]  = str(e)
            tlog.set_error(task_id, str(e))
            print(f"[ERROR] [{task_id}] {e}")

        finally:
            task_queue.task_done()


# ─── LIFESPAN ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Queue'lar process xotirasida. Ikkinchi app process alohida worker
    # ochib yubormasligi uchun bu DB connection lockni shutdowngacha ushlaydi.
    singleton_connection = acquire_worker_lock()

    key_store.init_db()
    users.init_db()
    settings_store.init_defaults(DEFAULT_PARALLEL_ACCOUNTS)

    # ── Admin yaratish ────────────────────────────────────────────────────────
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not users.admin_exists():
        if not admin_password:
            admin_password = str(uuid.uuid4())
            print(f"[WARN] ADMIN_PASSWORD .env da o'rnatilmagan!")
            print(f"[INFO] Tasodifiy parol: {admin_password}")
            print(f"[WARN] Bu parolni saqlang va .env ga ADMIN_PASSWORD qo'shing!")
        users.create_user("admin", admin_password, role="admin")
        print("[INFO] Birinchi admin yaratildi: admin")
    elif admin_password:
        users.set_password("admin", admin_password)
        print("[INFO] Admin paroli ADMIN_PASSWORD bo'yicha yangilandi")

    # ── DB dan task_status ni yuklash (restart bo'lganda) ────────────────────
    for t in tlog.list_all(limit=10000):
        task_status[t["task_id"]] = t
        if t["status"] == "running":
            task_status[t["task_id"]]["status"] = "error"
            task_status[t["task_id"]]["error"]  = "Server qayta ishga tushdi"
            tlog.set_error(t["task_id"], "Server qayta ishga tushdi")
        elif t["status"] == "queued":
            if t["service"] == "sponsored_search":
                task_status[t["task_id"]]["status"] = "error"
                task_status[t["task_id"]]["error"] = "Server qayta ishga tushdi"
                tlog.set_error(t["task_id"], "Server qayta ishga tushdi")
                continue
            meta = json.loads(t.get("task_meta") or "{}")
            await task_queue.put((t["priority"], _queue_timestamp(t.get("created_at")), t["task_id"], {
                "task_id":   t["task_id"],
                "service":   t["service"],
                "post_link": t["post_link"],
                "accounts":  meta.get("accounts"),
                "emoji":     meta.get("emoji", "👍"),
            }))

    background_tasks = [
        asyncio.create_task(sponsored_worker_loop()),
        asyncio.create_task(worker_loop()),
        asyncio.create_task(flood_release_loop()),
    ]
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        release_worker_lock(singleton_connection)


# ─── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Telegram Post Service API",
    description="Priority queue + User auth (admin/user) asosida",
    version="3.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


@app.get("/", include_in_schema=False)
async def frontend_root():
    return FileResponse(
        os.path.join(FRONTEND_DIR, "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


# ════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTLARI
# ════════════════════════════════════════════════════════════════════════════

@app.post("/auth/register", status_code=201)
async def register(req: RegisterRequest):
    """
    Ro'yxatdan o'tish.
    Birinchi admin yaratish uchun role='admin' berish mumkin.
    Keyinchalik admin yaratish uchun /admin/users ishlatiladi.
    """
    # Agar admin allaqachon mavjud bo'lsa — yangi adminni faqat admin yaratadi
    if req.role == users.ROLE_ADMIN and users.admin_exists():
        raise HTTPException(
            status_code=403,
            detail="Admin allaqachon mavjud. Yangi admin yaratish uchun /admin/users ga murojaat qiling"
        )
    try:
        user = users.create_user(req.username, req.password, req.role)
        api_key = key_store.ensure_for_user(user["id"])
        return {"message": "Ro'yxatdan o'tdingiz", **user, "api_key": api_key["api_key"]}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/auth/login")
async def login(req: LoginRequest):
    """Login. Muvaffaqiyatli bo'lsa token qaytaradi."""
    token = users.login(req.username, req.password)
    if not token:
        raise HTTPException(status_code=401, detail="Username yoki parol noto'g'ri")
    user = users.get_user_by_username(req.username)
    api_key = key_store.ensure_for_user(user["id"])
    return {
        "token": token,
        "username": req.username,
        "role": user["role"],
        "api_key": api_key["api_key"],
        "message": "Muvaffaqiyatli kirildi"
    }


@app.post("/auth/logout")
async def logout(x_token: Optional[str] = Header(default=None)):
    """Token o'chiradi."""
    if x_token:
        users.delete_token(x_token)
    return {"message": "Chiqildi"}


@app.get("/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    """Hozirgi foydalanuvchi ma'lumoti."""
    api_key = key_store.ensure_for_user(current_user["id"])
    return {**current_user, "api_key": api_key["api_key"]}


# ════════════════════════════════════════════════════════════════════════════
# TASK ENDPOINTLARI
# ════════════════════════════════════════════════════════════════════════════

async def _create_task(
    service: Literal["views", "reactions", "shares"],
    req: TaskRequest,
    priority: int,
    x_api_key: Optional[str],
) -> TaskResponse:
    """Umumiy task yaratish logikasi. Har uchta endpoint shu funksiyani ishlatadi."""
    try:
        parse_post_link(req.post_link)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    task_id    = str(uuid.uuid4())
    arrived_at = time.time()
    api_key    = x_api_key or ""

    # RAM da (tezkor o'qish uchun)
    task_status[task_id] = {
        "task_id":      task_id,
        "api_key":      api_key,
        "status":       "queued",
        "service":      service,
        "post_link":    req.post_link,
        "priority":     priority,
        "total":        0,
        "done":         0,
        "skipped":      0,
        "flooded":      0,
        "banned_count": 0,
        "error":        None,
    }

    # PostgreSQL da (tarixi uchun)
    tlog.create(task_id, api_key, service, req.post_link, priority,
                meta={"accounts": req.accounts})

    await task_queue.put((priority, arrived_at, task_id, {
        "task_id":   task_id,
        "service":   service,
        "post_link": req.post_link,
        "accounts":  req.accounts,
    }))

    print(f"[QUEUED] [{task_id}] key={api_key[:8]}… | priority={priority} | {service.upper()}")

    return TaskResponse(
        task_id=task_id,
        status="queued",
        priority=priority,
        message=f"Task qabul qilindi | priority={priority} | navbat={task_queue.qsize()}"
    )


@app.post("/task/views", response_model=TaskResponse)
@limiter.limit("30/minute")
async def create_views_task(
    request: Request,
    req: TaskRequest,
    key_context: dict = Depends(validate_user_api_key),
):
    """Views vazifasi yuborish. X-API-Key header majburiy."""
    return await _create_task("views", req, key_context["priority"], key_context["api_key"])


@app.post("/task/reactions", response_model=TaskResponse)
@limiter.limit("30/minute")
async def create_reactions_task(
    request: Request,
    req: ReactionTaskRequest,
    key_context: dict = Depends(validate_user_api_key),
):
    """Reactions vazifasi yuborish. X-API-Key header majburiy. reaction maydoni emoji bo'lishi kerak."""
    try:
        parse_post_link(req.post_link)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    task_id    = str(uuid.uuid4())
    arrived_at = time.time()
    api_key    = key_context["api_key"]
    priority   = key_context["priority"]

    task_status[task_id] = {
        "task_id":      task_id,
        "api_key":      api_key,
        "status":       "queued",
        "service":      "reactions",
        "post_link":    req.post_link,
        "priority":     priority,
        "total":        0,
        "done":         0,
        "skipped":      0,
        "flooded":      0,
        "banned_count": 0,
        "error":        None,
    }

    tlog.create(task_id, api_key, "reactions", req.post_link, priority,
                meta={"accounts": req.accounts, "emoji": req.reaction})

    await task_queue.put((priority, arrived_at, task_id, {
        "task_id":   task_id,
        "service":   "reactions",
        "post_link": req.post_link,
        "accounts":  req.accounts,
        "emoji":     req.reaction,
    }))

    print(f"[QUEUED] [{task_id}] key={api_key[:8]}… | priority={priority} | REACTIONS ({req.reaction})")

    return TaskResponse(
        task_id=task_id,
        status="queued",
        priority=priority,
        message=f"Task qabul qilindi | priority={priority} | navbat={task_queue.qsize()}"
    )


@app.post("/task/shares", response_model=TaskResponse)
@limiter.limit("30/minute")
async def create_shares_task(
    request: Request,
    req: TaskRequest,
    key_context: dict = Depends(validate_user_api_key),
):
    """Shares vazifasi yuborish. X-API-Key header majburiy."""
    return await _create_task("shares", req, key_context["priority"], key_context["api_key"])


async def _execute_sponsored_search(
    req: SponsoredSearchRequest,
    search_key: str,
    channel_username: str,
    queue_waited_seconds: float = 0,
    task_id: str | None = None,
):
    """Queue worker ichida sponsored qidiruvni bajaradi."""
    keyword = search_key

    sessions = session_store.get_active_sessions()
    if not sessions:
        raise HTTPException(status_code=409, detail="DB da active session topilmadi")

    errors: list[dict] = []
    sponsored_results: dict[tuple[str, int], dict] = {}
    checked = 0
    session_results: dict[str, dict] = {}
    state_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    searches_started = 0
    daily_skipped = 0
    blocked_skipped = 0
    busy_waited = 0
    target_found_sessions = 0
    stopped_early = False
    worker_tasks: set[asyncio.Task] = set()
    candidates: list[tuple[dict, str, asyncio.Lock]] = []

    def new_session_result(session_key: str) -> dict:
        return session_results.setdefault(session_key, {
            "session": session_key,
            "account": "",
            "checks": 0,
            "rounds_with_results": 0,
            "found_keys": set(),
            "found_ads": {},
            "views_sent": 0,
            "views_failed": 0,
            "view_errors": [],
            "stopped_early": False,
            "errors": [],
        })

    for session in sessions:
        session_key = session_store.get_session_key(session)
        if not session_key or session_store.is_blocked(session):
            blocked_skipped += 1
            continue
        if session_store.is_sponsored_keyword_done_today(session_key, keyword):
            daily_skipped += 1
            continue
        candidates.append((session, session_key, await get_lock(session)))
    eligible_sessions = len(candidates)

    async def search_one_session(
        session: dict,
        session_key: str,
        account_lock: asyncio.Lock,
    ) -> None:
        nonlocal checked, searches_started, daily_skipped, busy_waited
        nonlocal target_found_sessions, stopped_early

        was_busy = account_lock.locked()
        async with account_lock:
            while IS_PAUSED:
                await asyncio.sleep(2)

            if stop_event.is_set():
                return
            if not session_store.claim_sponsored_keyword_today(session_key, keyword):
                async with state_lock:
                    daily_skipped += 1
                return

            session_result = new_session_result(session_key)
            async with state_lock:
                searches_started += 1
                if was_busy:
                    busy_waited += 1

            result = await find_sponsored_peers(
                session,
                keyword,
                1,
                target_username=channel_username,
            )

        checked += 1
        if task_id:
            task_status[task_id]["done"] = checked
            tlog.inc_done(task_id)
        session_result["checks"] += 1
        status = result.get("status", "skip")
        if result.get("account"):
            session_result["account"] = result["account"]
        session_result["views_sent"] += int(result.get("views_sent") or 0)
        session_result["views_failed"] += int(result.get("views_failed") or 0)
        session_result["view_errors"].extend(result.get("view_errors") or [])

        if status == "ok":
            result_rows = result.get("rows", [])
            if result_rows:
                session_result["rounds_with_results"] += 1
            for row in result_rows:
                entity_key = (
                    str(row.get("entity_type") or "other"),
                    int(row.get("entity_id") or 0),
                )
                session_result["found_keys"].add(entity_key)
                session_result["found_ads"][entity_key] = {
                    "name": row.get("name") or "No name",
                    "username": row.get("username") or "",
                    "link": row.get("link") or "",
                    "type": row.get("type") or row.get("entity_type") or "other",
                    "target_match": bool(row.get("target_match")),
                    "view_sent": bool(row.get("view_sent")),
                }
                sponsored_result = sponsored_results.setdefault(entity_key, {
                    "row": row,
                    "sessions": {},
                    "sightings": 0,
                    "queries": set(),
                    "rounds": set(),
                })
                sponsored_result["sightings"] += 1
                sponsored_result["queries"].add(row.get("query_used") or keyword)
                sponsored_result["rounds"].add(1)
                viewer = sponsored_result["sessions"].setdefault(session_key, {
                    "session": session_key,
                    "account": result.get("account") or row.get("account") or "",
                    "sightings": 0,
                    "rounds": set(),
                })
                viewer["sightings"] += 1
                viewer["rounds"].add(1)

            if any(bool(row.get("target_match")) for row in result_rows):
                async with state_lock:
                    target_found_sessions += 1
                    if (
                        target_found_sessions >= SPONSORED_TARGET_STOP_THRESHOLD
                        and not stop_event.is_set()
                    ):
                        stopped_early = True
                        stop_event.set()

                if stop_event.is_set():
                    current_task = asyncio.current_task()
                    for task in worker_tasks:
                        if task is not current_task and not task.done():
                            task.cancel()
            return

        if status.startswith("flood:"):
            try:
                wait_seconds = int(status.split(":", 1)[1])
            except (IndexError, ValueError):
                wait_seconds = 300
            session_store.mark_flood(session, wait_seconds)
        elif status in ("banned", "auth"):
            session_store.mark_banned(session)

        error = {
            "session": session_key,
            "round": 1,
            "status": status,
            "error": result.get("error"),
        }
        errors.append(error)
        session_result["errors"].append(error)

    worker_count = min(req.parallel_sessions, req.accounts, len(candidates))

    async def run_selected(
        session: dict,
        session_key: str,
        account_lock: asyncio.Lock,
    ) -> None:
        try:
            await search_one_session(session, session_key, account_lock)
        except asyncio.CancelledError:
            if session_key in session_results:
                new_session_result(session_key)["stopped_early"] = True

    scheduled = 0
    while (
        (candidates or worker_tasks)
        and not stop_event.is_set()
        and (scheduled < req.accounts or worker_tasks)
    ):
        while (
            candidates
            and len(worker_tasks) < worker_count
            and scheduled < req.accounts
            and not stop_event.is_set()
        ):
            candidate = pop_first_unlocked(candidates)
            if candidate is None:
                break

            session, session_key, account_lock = candidate
            worker_tasks.add(asyncio.create_task(
                run_selected(session, session_key, account_lock)
            ))
            scheduled += 1

        if not worker_tasks:
            if candidates and scheduled < req.accounts:
                await asyncio.sleep(0.05)
                continue
            break

        done_tasks, pending_tasks = await asyncio.wait(
            worker_tasks,
            timeout=0.05 if candidates and scheduled < req.accounts else None,
            return_when=asyncio.FIRST_COMPLETED,
        )
        worker_tasks = set(pending_tasks)
        if done_tasks:
            await asyncio.gather(*done_tasks, return_exceptions=True)

    if worker_tasks:
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    serialized_session_results = []
    for item in session_results.values():
        found_count = len(item["found_keys"])
        if item["stopped_early"]:
            status = "stopped"
        elif found_count:
            status = "found"
        elif item["errors"]:
            status = "error"
        else:
            status = "not_found"
        serialized_session_results.append({
            "session": item["session"],
            "account": item["account"],
            "status": status,
            "checks": item["checks"],
            "rounds_with_results": item["rounds_with_results"],
            "found": found_count,
            "found_ads": list(item["found_ads"].values()),
            "views_sent": item["views_sent"],
            "views_failed": item["views_failed"],
            "view_errors": item["view_errors"],
            "stopped_early": item["stopped_early"],
            "errors": item["errors"],
        })

    sessions_found = sum(1 for item in serialized_session_results if item["status"] == "found")
    sessions_not_found = sum(1 for item in serialized_session_results if item["status"] == "not_found")
    sessions_failed = sum(1 for item in serialized_session_results if item["status"] == "error")
    sessions_stopped = sum(1 for item in serialized_session_results if item["status"] == "stopped")
    rows: list[dict] = []
    for item in sponsored_results.values():
        viewers = []
        for viewer in item["sessions"].values():
            viewers.append({
                "session": viewer["session"],
                "account": viewer["account"],
                "sightings": viewer["sightings"],
                "rounds": sorted(viewer["rounds"]),
            })
        rows.append({
            **item["row"],
            "sessions_count": len(viewers),
            "sessions": viewers,
            "sightings": item["sightings"],
            "queries": sorted(item["queries"]),
            "rounds_seen": sorted(item["rounds"]),
        })
    rows.sort(key=lambda item: (-item["sessions_count"], str(item.get("name") or "")))
    views_sent = sum(item["views_sent"] for item in serialized_session_results)
    views_failed = sum(item["views_failed"] for item in serialized_session_results)

    message = f"{len(rows)} ta unique sponsored natija topildi"
    if stopped_early:
        message += (
            f"; target {target_found_sessions} ta sessionda topildi va qidiruv to'xtatildi"
        )

    return {
        "message": message,
        "search_key": search_key,
        "keyword": keyword,
        "channel_username": channel_username,
        "search_method": "contacts.getSponsoredPeers",
        "view_method": "messages.viewSponsoredMessage",
        "view_rule": "non_matching_sponsored_only",
        "explicit_view_sent": views_sent > 0,
        "views_sent": views_sent,
        "views_failed": views_failed,
        "rounds": 1,
        "daily_keyword_rule": "one_keyword_per_session_per_day",
        "daily_skipped": daily_skipped,
        "blocked_skipped": blocked_skipped,
        "busy_waited": busy_waited,
        "target_found_sessions": target_found_sessions,
        "target_stop_threshold": SPONSORED_TARGET_STOP_THRESHOLD,
        "stopped_early": stopped_early,
        "sessions_requested": req.accounts,
        "sessions_available": len(sessions),
        "sessions_eligible": eligible_sessions,
        "sessions_started": searches_started,
        "parallel_sessions": worker_count,
        "queue_priority": SPONSORED_QUEUE_PRIORITY,
        "queue_waited_seconds": round(queue_waited_seconds, 3),
        "sessions_found": sessions_found,
        "sessions_not_found": sessions_not_found,
        "sessions_failed": sessions_failed,
        "sessions_stopped": sessions_stopped,
        "session_results": serialized_session_results,
        "checks_completed": checked,
        "found": len(rows),
        "results": rows,
        "errors": errors[:50],
    }


@app.post("/sponsored/search")
@limiter.limit("10/minute")
async def search_sponsored(
    request: Request,
    req: SponsoredSearchRequest,
    key_context: dict = Depends(validate_user_api_key),
):
    """Sponsored qidiruvni doimiy yuqori-prioritet queue ga qo'yadi."""
    search_key = " ".join(req.search_key.split())
    if not search_key:
        raise HTTPException(status_code=400, detail="Qidiruv key bo'sh bo'lmasligi kerak")

    raw_username = req.channel_username.strip()
    raw_username = re.sub(r"^https?://(?:www\.)?t\.me/", "", raw_username, flags=re.IGNORECASE)
    channel_username = raw_username.lstrip("@").split("/", 1)[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", channel_username):
        raise HTTPException(
            status_code=400,
            detail="Kanal username noto'g'ri. @kanal yoki https://t.me/kanal formatida kiriting",
        )

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    queued_at = time.time()
    task_id = str(uuid.uuid4())
    api_key = key_context["api_key"]
    log_meta = {
        "search_key": search_key,
        "channel_username": channel_username,
        "accounts": req.accounts,
        "parallel_sessions": req.parallel_sessions,
        "target_stop_threshold": SPONSORED_TARGET_STOP_THRESHOLD,
    }
    task_status[task_id] = {
        "task_id": task_id,
        "api_key": api_key,
        "status": "queued",
        "service": "sponsored_search",
        "post_link": f"https://t.me/{channel_username}",
        "priority": SPONSORED_QUEUE_PRIORITY,
        "total": 0,
        "done": 0,
        "skipped": 0,
        "flooded": 0,
        "banned_count": 0,
        "error": None,
    }
    tlog.create(
        task_id,
        api_key,
        "sponsored_search",
        f"https://t.me/{channel_username}",
        SPONSORED_QUEUE_PRIORITY,
        meta=log_meta,
    )

    await sponsored_priority.enqueue_sponsored(sponsored_queue, (
        SPONSORED_QUEUE_PRIORITY,
        queued_at,
        task_id,
        {
            "future": future,
            "req": req,
            "search_key": search_key,
            "channel_username": channel_username,
            "log_meta": log_meta,
        },
    ))

    print(
        f"[SPONSORED QUEUED] [{task_id}] priority={SPONSORED_QUEUE_PRIORITY} | "
        f"keyword={search_key} | waiting={sponsored_queue.qsize()}"
    )
    return await asyncio.shield(future)


@app.get("/status/{task_id}", response_model=StatusResponse)
async def get_status(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Task holati. DB dan olib, foizni hisoblaydi.
    Server qayta ishga tushsa ham tarixi saqlanadi.
    """
    api_key = key_store.ensure_for_user(current_user["id"])["api_key"]
    info = tlog.get_for_api_key(task_id, api_key)
    if not info:
        raise HTTPException(status_code=404, detail="Task topilmadi")
    # RAM dagi hisoblagichlarni qo'shish (faqat aktiv tasklar uchun)
    if task_id in task_status:
        info["skipped"]      = task_status[task_id].get("skipped", 0)
        info["flooded"]      = task_status[task_id].get("flooded", 0)
        info["banned_count"] = task_status[task_id].get("banned_count", 0)
    return StatusResponse(**info)


@app.get("/tasks")
async def list_tasks(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Barcha tasklar — DB dan, foiz va key bilan. Pagination: limit/offset."""
    api_key = key_store.ensure_for_user(current_user["id"])["api_key"]
    return tlog.list_by_api_key(api_key, limit=limit, offset=offset)


@app.get("/queue")
async def queue_info(_: dict = Depends(get_current_user)):
    normal_waiting = task_queue.qsize()
    sponsored_waiting = sponsored_queue.qsize()
    return {
        "waiting": normal_waiting + sponsored_waiting,
        "normal_waiting": normal_waiting,
        "sponsored_waiting": sponsored_waiting,
        "sponsored_has_priority": sponsored_priority.sponsored_active,
    }


@app.get("/locks")
async def lock_stats(_: dict = Depends(get_current_user)):
    return get_stats()


# ════════════════════════════════════════════════════════════════════════════
# ADMIN — API KEY ENDPOINTLARI
# ════════════════════════════════════════════════════════════════════════════

@app.post("/admin/keys", status_code=201)
async def admin_create_key(
    req: KeyCreateRequest,
    _: dict = Depends(require_admin),
):
    """Yangi API key yaratish (UUID auto-generatsiya)."""
    if req.user_id is not None:
        try:
            existing = key_store.ensure_for_user(req.user_id, req.priority)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        key_store.set_priority(existing["api_key"], req.priority)
        return {
            "message": "User API key tayyor",
            "api_key": existing["api_key"],
            "user_id": req.user_id,
            "priority": req.priority,
        }

    new_key = str(uuid.uuid4())
    key_store.register(new_key, req.priority)
    return {"message": "Kalit yaratildi", "api_key": new_key, "priority": req.priority}


@app.get("/admin/keys")
async def admin_list_keys(_: dict = Depends(require_admin)):
    """Barcha API kalitlar."""
    return key_store.list_all()


@app.patch("/admin/keys/{api_key}")
async def admin_update_priority(
    api_key: str,
    req: KeyPatchRequest,
    _: dict = Depends(require_admin),
):
    """API kalit prioritetini o'zgartirish."""
    if not key_store.set_priority(api_key, req.priority):
        raise HTTPException(status_code=404, detail="Kalit topilmadi")
    return {"api_key": api_key, "priority": req.priority}


@app.delete("/admin/keys/{api_key}")
async def admin_delete_key(api_key: str, _: dict = Depends(require_admin)):
    """API kalitni o'chirish."""
    if not key_store.delete_key(api_key):
        raise HTTPException(status_code=404, detail="Kalit topilmadi")
    return {"message": f"{api_key} o'chirildi"}


@app.get("/admin/stats")
async def admin_stats(_: dict = Depends(require_admin)):
    """
    Har bir API key uchun umumiy statistika:
    - Jami tasklar soni
    - Bajarilgan / xato / navbatda / ishlamoqda
    """
    return tlog.stats_by_key()


@app.get("/admin/tasks")
async def admin_all_tasks(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_admin),
):
    """Barcha tasklarni to'liq ko'rish. Pagination: limit/offset."""
    return tlog.list_all(limit=limit, offset=offset)


# ════════════════════════════════════════════════════════════════════════════
# ADMIN — USER ENDPOINTLARI
# ════════════════════════════════════════════════════════════════════════════

@app.get("/admin/users")
async def admin_list_users(_: dict = Depends(require_admin)):
    """Barcha foydalanuvchilar."""
    return users.list_users()


@app.post("/admin/users", status_code=201)
async def admin_create_user(
    req: RegisterRequest,
    _: dict = Depends(require_admin),
):
    """Admin tomonidan foydalanuvchi yaratish (har qanday rol)."""
    try:
        user = users.create_user(req.username, req.password, req.role)
        api_key = key_store.ensure_for_user(user["id"])
        return {"message": "Foydalanuvchi yaratildi", **user, "api_key": api_key["api_key"]}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    current_user: dict = Depends(require_admin),
):
    """Foydalanuvchini o'chirish (o'zini o'chira olmaydi)."""
    if current_user["id"] == user_id:
        raise HTTPException(status_code=400, detail="O'zingizni o'chira olmaysiz")
    if not users.delete_user(user_id):
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    return {"message": f"User #{user_id} o'chirildi"}


# ════════════════════════════════════════════════════════════════════════════
# ADMIN — SESSION / REDIS ENDPOINTLARI
# ════════════════════════════════════════════════════════════════════════════

def _get_redis() -> aioredis.Redis:
    """Sync wrapper — async redis klientini qaytaradi."""
    return aioredis.from_url(REDIS_URL, decode_responses=True)


def _upsert_telegram_session_from_dict(
    db,
    data: dict,
    fallback_uid: str,
    update: bool,
    default_status: str = "active",
) -> str:
    session_value = data.get("session") or data.get("hash_session")
    api_id = data.get("app_id") or data.get("api_id")
    api_hash = data.get("app_hash") or data.get("api_hash")
    if not (session_value and api_id and api_hash):
        raise ValueError("session/hash_session, app_id/api_id, app_hash/api_hash majburiy")

    uid = (
        data.get("uid") or
        data.get("user_id") or
        data.get("number") or
        data.get("phone") or
        fallback_uid
    )
    uid = str(uid).strip()
    if not uid:
        raise ValueError("uid topilmadi")

    row = db.query(TelegramSession).filter_by(uid=uid).first()
    if row and not update:
        return "skipped"

    if not row:
        row = TelegramSession(
            uid=uid,
            api_id=str(api_id),
            api_hash=str(api_hash),
            hash_session=str(session_value),
            is_premium=str(data.get("is_premium") or "0"),
            is_scam=str(data.get("is_scam") or "no"),
            is_working=str(data.get("is_working") or default_status),
        )
        db.add(row)
        action = "inserted"
    else:
        row.api_id = str(api_id)
        row.api_hash = str(api_hash)
        row.hash_session = str(session_value)
        row.is_working = str(data.get("is_working") or row.is_working or default_status)
        action = "updated"

    row.phone_model = data.get("device") or data.get("phone_model") or row.phone_model
    row.is_premium = str(data.get("is_premium") or row.is_premium or "0")
    row.username = data.get("username") or row.username
    row.user_id = str(data.get("user_id") or "") or row.user_id
    row.phone_num = str(data.get("number") or data.get("phone") or data.get("phone_num") or "") or row.phone_num
    row.app_version = data.get("app_version") or row.app_version
    row.is_scam = str(data.get("is_scam") or row.is_scam or "no")
    row.app_name = data.get("app_name") or row.app_name
    row.lang_code = data.get("lang_code") or row.lang_code
    row.pid_id = str(data.get("pid_id") or "") or row.pid_id
    row.date_last_online = data.get("date_last_online") or row.date_last_online
    return action


@app.post("/admin/sessions/upload-folder")
async def admin_upload_folder_to_db(
    sessions_dir: str = Query(default="sessions", description="Session JSON papkasi"),
    clear_first: bool = Query(default=False, description="DB dagi eski sessionlarni o'chirish"),
    _: dict = Depends(require_admin),
):
    """sessions/ papkasidagi JSON fayllarni faqat DB ga saqlaydi."""
    import glob

    if not os.path.isdir(sessions_dir):
        raise HTTPException(status_code=400, detail=f"Papka topilmadi: {sessions_dir}")

    files = glob.glob(os.path.join(sessions_dir, "*.json"))
    if not files:
        raise HTTPException(status_code=400, detail="Papkada .json fayl yo'q")

    inserted = updated = skipped = 0
    errors: list[str] = []

    with SessionLocal() as db:
        if clear_first:
            db.query(TelegramSession).delete()

        for fpath in sorted(files):
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)

                if not (data.get("session") and data.get("app_id") and data.get("app_hash")):
                    skipped += 1
                    errors.append(f"{os.path.basename(fpath)}: session/app_id/app_hash yo'q")
                    continue

                uid = (
                    data.get("uid") or
                    data.get("user_id") or
                    data.get("number") or
                    os.path.splitext(os.path.basename(fpath))[0]
                )
                uid = str(uid)
                row = db.query(TelegramSession).filter_by(uid=uid).first()

                if not row:
                    row = TelegramSession(
                        uid=uid,
                        api_id=str(data.get("app_id")),
                        api_hash=str(data.get("app_hash")),
                        hash_session=str(data.get("session")),
                        is_premium=str(data.get("is_premium") or "0"),
                        is_scam=str(data.get("is_scam") or "no"),
                        is_working=str(data.get("is_working") or "active"),
                    )
                    db.add(row)
                    inserted += 1
                else:
                    row.api_id = str(data.get("app_id") or row.api_id)
                    row.api_hash = str(data.get("app_hash") or row.api_hash)
                    row.hash_session = str(data.get("session") or row.hash_session)
                    row.is_working = str(data.get("is_working") or row.is_working or "active")
                    updated += 1

                row.phone_model = data.get("device") or data.get("phone_model") or row.phone_model
                row.user_id = str(data.get("user_id") or "") or row.user_id
                row.phone_num = str(data.get("number") or data.get("phone") or "") or row.phone_num
                row.username = data.get("username") or row.username
                row.app_version = data.get("app_version") or row.app_version
                row.app_name = data.get("app_name") or row.app_name
                row.lang_code = data.get("lang_code") or row.lang_code
            except Exception as e:
                skipped += 1
                errors.append(f"{os.path.basename(fpath)}: {e}")

        db.commit()

    return {
        "message": f"{inserted} yangi, {updated} yangilandi, {skipped} o'tkazildi",
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "active_total": session_store.count_active_sessions(),
        "errors": errors[:20],
    }


@app.post("/admin/sessions/import-json")
async def admin_import_json_sessions(
    files: list[UploadFile] = File(..., description="Session JSON fayllari"),
    update: bool = Query(default=True, description="uid mavjud bo'lsa yangilash"),
    default_status: Literal["active", "sleep"] = Query(default="active", description="Yangi session statusi"),
    _: dict = Depends(require_admin),
):
    """Brauzerdan yuborilgan JSON session fayllarni DB ga saqlaydi."""
    inserted = updated = skipped = 0
    errors: list[str] = []

    with SessionLocal() as db:
        for file in files:
            filename = file.filename or "session.json"
            if not filename.lower().endswith(".json"):
                skipped += 1
                errors.append(f"{filename}: faqat .json qabul qilinadi")
                continue

            raw = await file.read()
            try:
                text = raw.decode("utf-8-sig")
                parsed = json.loads(text)
            except Exception as e:
                skipped += 1
                errors.append(f"{filename}: JSON o'qilmadi ({e})")
                continue

            payloads = parsed if isinstance(parsed, list) else [parsed]
            for index, payload in enumerate(payloads, start=1):
                if not isinstance(payload, dict):
                    skipped += 1
                    errors.append(f"{filename}#{index}: obyekt emas")
                    continue
                fallback_uid = os.path.splitext(filename)[0]
                if len(payloads) > 1:
                    fallback_uid = f"{fallback_uid}-{index}"
                try:
                    action = _upsert_telegram_session_from_dict(
                        db,
                        payload,
                        fallback_uid=fallback_uid,
                        update=update,
                        default_status=default_status,
                    )
                    if action == "inserted":
                        inserted += 1
                    elif action == "updated":
                        updated += 1
                    else:
                        skipped += 1
                except Exception as e:
                    skipped += 1
                    errors.append(f"{filename}#{index}: {e}")

        db.commit()

    return {
        "message": f"{inserted} yangi, {updated} yangilandi, {skipped} o'tkazildi",
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "active_total": session_store.count_active_sessions(),
        "errors": errors[:20],
    }


@app.post("/admin/sessions/import-raw")
async def admin_import_raw_sessions(
    req: RawSessionsRequest,
    _: dict = Depends(require_admin),
):
    """Har qatorda bittadan StringSession bo'lgan matnni DB ga saqlaydi."""
    lines = [line.strip() for line in req.sessions.splitlines() if line.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="Session qatori topilmadi")

    inserted = updated = skipped = 0
    errors: list[str] = []

    with SessionLocal() as db:
        for index, session_line in enumerate(lines, start=1):
            fallback_uid = hashlib.sha256(session_line.encode()).hexdigest()[:24]
            try:
                action = _upsert_telegram_session_from_dict(
                    db,
                    {
                        "session": session_line,
                        "api_id": req.api_id,
                        "api_hash": req.api_hash,
                    },
                    fallback_uid=fallback_uid,
                    update=req.update,
                    default_status=req.default_status,
                )
                if action == "inserted":
                    inserted += 1
                elif action == "updated":
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                skipped += 1
                errors.append(f"Qator {index}: {e}")

        db.commit()

    return {
        "message": f"{inserted} yangi, {updated} yangilandi, {skipped} o'tkazildi",
        "api_id": req.api_id,
        "api_hash": req.api_hash,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "active_total": session_store.count_active_sessions(),
        "errors": errors[:20],
    }


@app.post("/admin/sessions/upload-folder-redis", include_in_schema=False)
async def admin_upload_from_folder(
    sessions_dir: str = Query(default="sessions", description="Session JSON papkasi"),
    clear_first: bool = Query(default=False, description="Yuklashdan oldin Redis ni tozalash"),
    _: dict = Depends(require_admin),
):
    raise HTTPException(status_code=410, detail="Sessionlar faqat DB ga saqlanadi; /admin/sessions/upload-folder ishlating")
    """
    **sessions/** papkasidagi JSON fayllarni Redis ga yuklaydi.

    Har bir fayl quyidagi fieldlarga ega bo'lishi kerak:
    `session`, `app_id`, `app_hash`

    - `sessions_dir` — papka yo'li (default: `sessions/`)
    - `clear_first`  — `true` bo'lsa avval Redis ni tozalaydi
    """
    import glob

    if not os.path.isdir(sessions_dir):
        raise HTTPException(status_code=400, detail=f"Papka topilmadi: {sessions_dir}")

    files = glob.glob(os.path.join(sessions_dir, "*.json"))
    if not files:
        raise HTTPException(status_code=400, detail="Papkada .json fayl yo'q")

    loaded, skipped = [], []
    for fpath in sorted(files):
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            if not (data.get("session") and data.get("app_id") and data.get("app_hash")):
                skipped.append(os.path.basename(fpath))
                continue
            loaded.append({
                "session":  data["session"],
                "app_id":   int(data["app_id"]),
                "app_hash": data["app_hash"],
                "proxy":    data.get("proxy"),
                "number":   data.get("number"),
                "user_id":  data.get("user_id"),
                "device":   data.get("device"),
            })
        except Exception as e:
            skipped.append(f"{os.path.basename(fpath)}: {e}")

    if not loaded:
        raise HTTPException(status_code=400, detail="Yuklash uchun yaroqli session topilmadi")

    # Banned sessionlarni DB dan tekshirib filtrlash
    banned_numbers: set[str] = set()
    with SessionLocal() as db:
        banned_rows = db.query(TelegramSession).filter(
            TelegramSession.is_working == "banned"
        ).all()
        for r in banned_rows:
            if r.phone_num:
                banned_numbers.add(r.phone_num)
            if r.user_id:
                banned_numbers.add(str(r.user_id))

    before = len(loaded)
    loaded = [s for s in loaded if
              not (s.get("number") in banned_numbers or
                   str(s.get("user_id", "")) in banned_numbers)]
    banned_filtered = before - len(loaded)

    rm = RedisSessionManager(REDIS_URL)
    try:
        if clear_first:
            await rm.redis.delete(REDIS_KEY)

        raise HTTPException(status_code=410, detail="Redis upload o'chirilgan")
        total = await rm.redis.scard(REDIS_KEY)
    finally:
        await rm.close()

    return {
        "message":        f"{len(loaded)} ta session yuklandi",
        "loaded":         len(loaded),
        "skipped_files":  len(skipped),
        "banned_filtered": banned_filtered,
        "skipped_names":  skipped,
        "redis_total":    total,
    }


@app.post("/admin/sessions/upload-db")
async def admin_upload_from_db(
    only_active: bool = Query(default=True, description="Faqat is_working='active' bo'lganlarni sanash"),
    _: dict = Depends(require_admin),
):
    """Sessionlar DBdan ishlaydi; Redisga yuklash talab qilinmaydi."""
    with SessionLocal() as db:
        query = db.query(TelegramSession)
        if only_active:
            query = query.filter(TelegramSession.is_working == "active")
        total = query.count()

    return {
        "message": "Sessionlar faqat DB orqali ishlaydi; Redisga yuklanmadi",
        "loaded": 0,
        "db_total": total,
        "only_active": only_active,
    }


@app.post("/admin/sessions/upload-db-redis", include_in_schema=False)
async def admin_upload_from_db_redis(
    clear_first: bool = Query(default=False, description="Yuklashdan oldin Redis ni tozalash"),
    only_active: bool = Query(default=True,  description="Faqat is_working='active' bo'lganlarni yuklash"),
    _: dict = Depends(require_admin),
):
    raise HTTPException(status_code=410, detail="Sessionlar DB dan ishlaydi; Redisga yuklash o'chirilgan")
    """
    **telegram_sessions** jadvalidagi akkauntlarni Redis ga yuklaydi.

    - `only_active` — `true` bo'lsa faqat `is_working='active'` akkauntlar yuklaydi
    - `clear_first` — `true` bo'lsa avval Redis ni tozalaydi
    """
    with SessionLocal() as db:
        query = db.query(TelegramSession)
        if only_active:
            query = query.filter(TelegramSession.is_working == "active")
        rows = query.all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Ma'lumotlar bazasida mos session topilmadi"
        )

    sessions = []
    for row in rows:
        try:
            sessions.append({
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

    rm = RedisSessionManager(REDIS_URL)
    try:
        if clear_first:
            await rm.redis.delete(REDIS_KEY)

        raise HTTPException(status_code=410, detail="Redis upload o'chirilgan")
        total = await rm.redis.scard(REDIS_KEY)
    finally:
        await rm.close()

    return {
        "message":     f"{len(sessions)} ta session DB dan yuklandi",
        "loaded":      len(sessions),
        "only_active": only_active,
        "redis_total": total,
    }


@app.delete("/admin/sessions/clear-redis")
async def admin_clear_redis(_: dict = Depends(require_admin)):
    """
    Redis dagi barcha sessionlarni **o'chiradi**.

    > ⚠️ Bu amalni bajargandan so'ng barcha tasklarni qayta session yuklashsiz  
    > yuborish mumkin emas!
    """
    r = _get_redis()
    try:
        old_count = await r.scard(REDIS_KEY)
        await r.delete(REDIS_KEY)
    finally:
        await r.aclose()

    return {
        "message":   f"Redis tozalandi — {old_count} ta session o'chirildi",
        "deleted":   old_count,
        "redis_key": REDIS_KEY,
    }


@app.post("/admin/sessions/import-csv")
async def admin_import_csv(
    file: UploadFile = File(..., description="CSV/TXT fayl (headerli CSV yoki har qatorda bitta StringSession)"),
    update: bool = Query(default=True, description="uid mavjud bo'lsa yangilash"),
    _: dict = Depends(require_admin),
):
    """
    CSV fayldan **telegram_sessions** jadvaliga session yuklaydi.

    **CSV ustunlari** (header qatori majburiy):
    `uid`, `api_id`, `api_hash`, `hash_session`, `phone_model`,
    `is_premium`, `username`, `user_id`, `phone_num`, `app_version`,
    `is_scam`, `app_name`, `lang_code`, `is_working`, `pid_id`, `date_last_online`

    - `update=true`  — `uid` mavjud bo'lsa ustidagi ma'lumotlarni yangilaydi
    - `update=false` — `uid` mavjud bo'lsa o'tkazib yuboradi
    """
    filename = file.filename or "sessions.csv"
    if not filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(status_code=400, detail="Faqat .csv yoki .txt fayl qabul qilinadi")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # BOM ni ham qabul qiladi
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    required = {"uid", "api_id", "api_hash", "hash_session"}
    if not required.issubset(set(reader.fieldnames or [])):
        inserted = updated = skipped = 0
        errors: list[str] = []
        rows = csv.reader(io.StringIO(text))
        with SessionLocal() as db:
            for i, row in enumerate(rows, start=1):
                if not row:
                    continue
                session_line = (row[0] or "").strip()
                if not session_line or session_line.lower() in {"session", "hash_session", "string_session"}:
                    continue
                fallback_uid = hashlib.sha256(session_line.encode()).hexdigest()[:24]
                try:
                    action = _upsert_telegram_session_from_dict(
                        db,
                        {
                            "session": session_line,
                            "api_id": DEFAULT_SESSION_API_ID,
                            "api_hash": DEFAULT_SESSION_API_HASH,
                        },
                        fallback_uid=fallback_uid,
                        update=update,
                        default_status="active",
                    )
                    if action == "inserted":
                        inserted += 1
                    elif action == "updated":
                        updated += 1
                    else:
                        skipped += 1
                except Exception as e:
                    skipped += 1
                    errors.append(f"Qator {i}: {e}")
            db.commit()

        return {
            "message": f"Header topilmadi; raw session sifatida yuklandi: {inserted} yangi, {updated} yangilandi, {skipped} o'tkazildi",
            "mode": "raw",
            "api_id": DEFAULT_SESSION_API_ID,
            "api_hash": DEFAULT_SESSION_API_HASH,
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "active_total": session_store.count_active_sessions(),
            "errors": errors[:20],
        }

    inserted = updated = skipped = 0
    errors: list[str] = []

    with SessionLocal() as db:
        for i, row in enumerate(reader, start=2):  # 2-qatordan (1-header)
            uid = (row.get("uid") or "").strip()
            if not uid:
                errors.append(f"Qator {i}: uid bo'sh")
                skipped += 1
                continue

            if not row.get("hash_session", "").strip():
                errors.append(f"Qator {i} ({uid}): hash_session bo'sh")
                skipped += 1
                continue

            existing = db.query(TelegramSession).filter_by(uid=uid).first()

            if existing:
                if not update:
                    skipped += 1
                    continue
                # Yangilash
                existing.api_id           = (row.get("api_id")      or existing.api_id).strip()
                existing.api_hash         = (row.get("api_hash")     or existing.api_hash).strip()
                existing.hash_session     = (row.get("hash_session") or existing.hash_session).strip()
                existing.phone_model      = (row.get("phone_model")  or existing.phone_model or "").strip() or None
                existing.is_premium       = (row.get("is_premium")   or existing.is_premium or "0").strip()
                existing.username         = (row.get("username")     or "").strip() or None
                existing.user_id          = (row.get("user_id")      or existing.user_id or "").strip() or None
                existing.phone_num        = (row.get("phone_num")    or existing.phone_num or "").strip() or None
                existing.app_version      = (row.get("app_version")  or "").strip() or None
                existing.is_scam          = (row.get("is_scam")      or existing.is_scam or "no").strip()
                existing.app_name         = (row.get("app_name")     or "").strip() or None
                existing.lang_code        = (row.get("lang_code")    or "").strip() or None
                existing.is_working       = (row.get("is_working")   or existing.is_working or "active").strip()
                existing.pid_id           = (row.get("pid_id")       or "").strip() or None
                existing.date_last_online = (row.get("date_last_online") or "").strip() or None
                updated += 1
            else:
                # Yangi qo'shish
                try:
                    db.add(TelegramSession(
                        uid              = uid,
                        api_id           = row.get("api_id",           "2040").strip(),
                        api_hash         = row.get("api_hash",         "").strip(),
                        hash_session     = row.get("hash_session",     "").strip(),
                        phone_model      = (row.get("phone_model")     or "").strip() or None,
                        is_premium       = (row.get("is_premium")      or "0").strip(),
                        username         = (row.get("username")        or "").strip() or None,
                        user_id          = (row.get("user_id")         or "").strip() or None,
                        phone_num        = (row.get("phone_num")       or "").strip() or None,
                        app_version      = (row.get("app_version")     or "").strip() or None,
                        is_scam          = (row.get("is_scam")         or "no").strip(),
                        app_name         = (row.get("app_name")        or "").strip() or None,
                        lang_code        = (row.get("lang_code")       or "").strip() or None,
                        is_working       = (row.get("is_working")      or "active").strip(),
                        pid_id           = (row.get("pid_id")          or "").strip() or None,
                        date_last_online = (row.get("date_last_online") or "").strip() or None,
                    ))
                    inserted += 1
                except Exception as e:
                    errors.append(f"Qator {i} ({uid}): {e}")
                    skipped += 1

        db.commit()

    return {
        "message":  f"CSV import tugadi: {inserted} yangi, {updated} yangilandi, {skipped} o'tkazildi",
        "inserted": inserted,
        "updated":  updated,
        "skipped":  skipped,
        "errors":   errors[:20],
    }


# ════════════════════════════════════════════════════════════════════════════
# ADMIN — SESSION MONITORING & CIRCUIT BREAKER
# ════════════════════════════════════════════════════════════════════════════

@app.get("/admin/sessions/stats")
async def admin_session_stats(_: dict = Depends(require_admin)):
    """
    Session holat statistikasi DB dan olinadi.
    """
    return session_store.get_stats()


@app.get("/admin/system/status")
async def admin_system_status(_: dict = Depends(require_admin)):
    """
    Tizim holati (circuit breaker):
    - is_paused: to'xtatilganmi?
    - paused_reason: to'xtatilish sababi
    - recent_errors: so'nggi xatolar soni
    """
    return {
        "is_paused":     IS_PAUSED,
        "paused_reason": PAUSED_REASON,
        "recent_errors": len(_cb_errors),
        "cb_threshold":  CB_THRESHOLD,
        "cb_window_sec": CB_WINDOW,
        "queue_size":    task_queue.qsize() + sponsored_queue.qsize(),
        "normal_queue_size": task_queue.qsize(),
        "sponsored_queue_size": sponsored_queue.qsize(),
        "sponsored_has_priority": sponsored_priority.sponsored_active,
        "parallel_accounts": settings_store.get_parallel_accounts(DEFAULT_PARALLEL_ACCOUNTS),
        "parallel_accounts_default": DEFAULT_PARALLEL_ACCOUNTS,
    }


@app.patch("/admin/system/settings")
async def admin_update_system_settings(
    req: SystemSettingsRequest,
    _: dict = Depends(require_admin),
):
    parallel_accounts = settings_store.set_parallel_accounts(req.parallel_accounts)
    return {
        "message": "Sozlamalar saqlandi",
        "parallel_accounts": parallel_accounts,
    }


@app.post("/admin/sessions/resume")
async def admin_resume_system(_: dict = Depends(require_admin)):
    """
    Circuit breaker ni o'chirib tizimni qayta ishga tushiradi.

    To'xtatilgan tasklar **to'xtagan joyidan** davom etadi —
    hech narsa yo'qolmaydi.
    """
    global IS_PAUSED, PAUSED_REASON, _cb_errors

    was_paused = IS_PAUSED
    IS_PAUSED     = False
    PAUSED_REASON = ""
    _cb_errors    = []

    print("[RESUME] Admin tizimni qayta ishga tushirdi.")
    return {
        "message":   "Tizim qayta ishga tushirildi. Tasklar davom etadi.",
        "was_paused": was_paused,
        "queue_size": task_queue.qsize() + sponsored_queue.qsize(),
    }


# ─── ROOT ────────────────────────────────────────────────────────────────────
@app.get("/api")
async def root():
    return {
        "version": "4.0.0",
        "auth": {
            "POST /auth/register": "Ro'yxatdan o'tish",
            "POST /auth/login":    "Login → token",
            "POST /auth/logout":   "Logout",
            "GET  /auth/me":       "Mening ma'lumotlarim",
        },
        "task": {
            "POST /task":           "Vazifa yuborish (X-API-Key)",
            "GET  /status/{id}":    "Task holati + foiz + key + vaqt",
            "GET  /tasks":          "Barcha tasklarning tarixi (DB)",
            "GET  /queue":          "Navbat uzunligi",
            "GET  /locks":          "Akkaunt holati",
        },
        "sponsored": {
            "POST /sponsored/search": "Telegram qidiruvidagi sponsored/reklama natijalarini topish",
        },
        "admin (X-Token kerak)": {
            "POST   /admin/keys":        "API key yaratish",
            "GET    /admin/keys":        "Kalitlar ro'yxati",
            "PATCH  /admin/keys/{key}":  "Prioritet o'zgartirish",
            "DELETE /admin/keys/{key}":  "Kalitni o'chirish",
            "GET    /admin/stats":       "Key bo'yicha statistika",
            "GET    /admin/tasks":       "Barcha tasklar (admin)",
            "GET    /admin/users":       "Foydalanuvchilar",
            "POST   /admin/users":       "Foydalanuvchi yaratish",
            "DELETE /admin/users/{id}":  "Foydalanuvchini o'chirish",
            "POST   /admin/sessions/import-csv":    "CSV fayldan DB ga yuklash",
            "POST   /admin/sessions/upload-folder": "Papkadan Redis ga yuklash",
            "POST   /admin/sessions/upload-db":     "DB dan Redis ga yuklash",
            "DELETE /admin/sessions/clear-redis":   "Redis ni tozalash",
            "GET    /admin/sessions/stats":         "Session holat statistika",
            "GET    /admin/system/status":          "Tizim holati (circuit breaker)",
            "POST   /admin/sessions/resume":        "Tizimni qayta ishga tushirish",
        }
    }


# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
