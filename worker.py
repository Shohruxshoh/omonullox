"""
worker.py — API siz ham ishlatish uchun standalone script.

Ishlatish:
    python worker.py

Foydalanuvchi post linkini va xizmat turini kiritadi.
"""

import asyncio
import random
import time

from redis_main import RedisSessionManager
from post_services import SERVICE_MAP, parse_post_link
from account_queue import get_lock
from worker_guard import acquire_worker_lock, release_worker_lock, WorkerAlreadyRunningError
import session_store

REDIS_KEY = "telegram:sessions:full"
REDIS_URL = "redis://localhost:6379"
PARALLEL_ACCOUNTS = 400


async def main():
    redis_manager = RedisSessionManager(REDIS_URL)

    # ── 1. Post linkni olish ──────────────────────────────────────────────
    post_link = input("🔗 Post linki (https://t.me/channel/123): ").strip()

    try:
        channel, msg_id = parse_post_link(post_link)
    except ValueError as e:
        print(f"❌ {e}")
        return

    # ── 2. Xizmat turini tanlash ─────────────────────────────────────────
    print("\nQaysi xizmat bajarilsin?")
    print("  1️⃣   Views")
    print("  2️⃣   Reactions")
    print("  3️⃣   Shares")

    choice = input("Tanlovingiz (1/2/3): ").strip()
    service = {"1": "views", "2": "reactions", "3": "shares"}.get(choice)

    if not service:
        print("❌ Noto'g'ri tanlov")
        return

    action_func = SERVICE_MAP[service]

    # ── 3. Nechta akkaunt? ────────────────────────────────────────────────
    limit_input = input("Nechta akkaunt? (bo'sh qoldirsa — barchasi): ").strip()
    limit = int(limit_input) if limit_input.isdigit() else None

    # ── 4. Sessionlarni Redis dan olish ──────────────────────────────────
    sessions = await redis_manager.get_all_sessions(REDIS_KEY)
    if not sessions:
        print("❌ Redis bo'sh — session topilmadi")
        return

    random.shuffle(sessions)
    if limit:
        sessions = sessions[:limit]

    total = len(sessions)
    print(f"\n🚀 {total} akkaunt | {service.upper()} | post: {post_link}\n")

    # ── 5. Parallel ishga tushirish ───────────────────────────────────────
    semaphore = asyncio.Semaphore(PARALLEL_ACCOUNTS)
    done = 0
    start = time.perf_counter()

    async def run_one(session):
        nonlocal done
        async with semaphore:
            # ═══════════════════════════════════════════════
            # PER-ACCOUNT LOCK: bitta akkaunt hech qachon parallel
            # ishlamaydi. Agar oldingi ishlatish tugamagan bo'lsa
            # bu yerda kutadi.
            # ═══════════════════════════════════════════════
            account_lock = await get_lock(session)
            async with account_lock:
                await asyncio.sleep(random.uniform(0.1, 0.5))
                session_store.mark_used(session)
                await action_func(session, channel, msg_id)
                done += 1
                if done % 50 == 0 or done == total:
                    elapsed = time.perf_counter() - start
                    print(f"  ↳ {done}/{total} | {elapsed:.1f}s")

    await asyncio.gather(*(run_one(s) for s in sessions))

    elapsed = time.perf_counter() - start
    print(f"\n✅ {service.upper()} tugadi | {total} akkaunt | {elapsed:.2f}s")

    await redis_manager.close()


async def guarded_main():
    singleton_connection = acquire_worker_lock()
    try:
        await main()
    finally:
        release_worker_lock(singleton_connection)


if __name__ == "__main__":
    try:
        asyncio.run(guarded_main())
    except WorkerAlreadyRunningError as e:
        raise SystemExit(f"Worker ishga tushmadi: {e}") from e
    except KeyboardInterrupt:
        print("\n⛔ To'xtatildi")
