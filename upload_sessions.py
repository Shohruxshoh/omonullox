"""
upload_sessions.py — sessions/ papkasidagi JSON fayllarni Redis ga yuklaydi.

Har bir JSON fayl quyidagi ko'rinishda bo'lishi kerak:
    {
        "app_id":   "2040",
        "app_hash": "b18441a1ff607e10a989891a5462e627",
        "session":  "1ApWapzMBu...",
        "device":   "TCL 10 Plus",
        "user_id":  "8326609315",
        "number":   "998870256185"
    }

Ishlatish:
    python upload_sessions.py                    # sessions/ papkasidan yuklash
    python upload_sessions.py --dir my_sessions  # boshqa papka
    python upload_sessions.py --count            # nechta bor?
    python upload_sessions.py --list             # barchasini ko'rish
    python upload_sessions.py --clear            # tozalash
"""

import argparse
import json
import os
import sys

import redis


DEFAULT_REDIS_URL    = os.environ.get("REDIS_URL", "redis://localhost:6379")
DEFAULT_KEY          = os.environ.get("REDIS_KEY", "telegram:sessions:full")
DEFAULT_SESSIONS_DIR = "sessions"


def load_json_files(directory: str) -> list[dict]:
    """Papkadagi barcha .json fayllarni o'qiydi va Redis formatiga o'tkazadi."""
    if not os.path.isdir(directory):
        print(f"[ERROR] Papka topilmadi: {directory}")
        sys.exit(1)

    files = [f for f in os.listdir(directory) if f.endswith(".json")]
    if not files:
        print(f"[WARN]  '{directory}' papkasida .json fayl yo'q")
        sys.exit(1)

    sessions = []
    errors = 0
    for fname in sorted(files):
        path = os.path.join(directory, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            if not data.get("session") or not data.get("app_id") or not data.get("app_hash"):
                print(f"  [SKIP] {fname}: session/app_id/app_hash yo'q")
                errors += 1
                continue

            sessions.append({
                "session":  data["session"],
                "app_id":   int(data["app_id"]),
                "app_hash": data["app_hash"],
                "proxy":    data.get("proxy"),
                "number":   data.get("number"),
                "user_id":  data.get("user_id"),
                "device":   data.get("device"),
            })

        except json.JSONDecodeError as e:
            print(f"  [ERR]  {fname}: JSON xato - {e}")
            errors += 1

    print(f"[INFO]  {directory}/ -> {len(sessions)} ta session o'qildi, {errors} ta xato")
    return sessions


def push_sessions(r: redis.Redis, key: str, sessions: list[dict]) -> int:
    pipe = r.pipeline()
    for s in sessions:
        pipe.sadd(key, json.dumps(s, ensure_ascii=False))
    pipe.execute()
    return len(sessions)


def main():
    parser = argparse.ArgumentParser(description="sessions/ papkasini Redis ga yuklash")
    parser.add_argument("--redis",  default=DEFAULT_REDIS_URL,    help="Redis URL")
    parser.add_argument("--key",    default=DEFAULT_KEY,           help="Redis SET kalit nomi")
    parser.add_argument("--dir",    default=DEFAULT_SESSIONS_DIR,  help="Session papkasi")
    parser.add_argument("--clear",  action="store_true",           help="Yuklashdan oldin tozalash")
    parser.add_argument("--count",  action="store_true",           help="Redis dagi sonni ko'rsatish")
    parser.add_argument("--list",   action="store_true",           help="Barcha sessionlarni chiqarish")
    args = parser.parse_args()

    # Redis ulanish
    try:
        r = redis.from_url(args.redis, decode_responses=True)
        r.ping()
        print(f"[OK]    Redis: {args.redis}  |  key: {args.key}")
    except Exception as e:
        print(f"[ERROR] Redis ga ulanib bo'lmadi: {e}")
        sys.exit(1)

    # Son ko'rish
    if args.count:
        n = r.scard(args.key)
        print(f"[INFO]  Redis da {n} ta session bor")
        return

    # Barchasini chiqarish
    if args.list:
        members = r.smembers(args.key)
        if not members:
            print("[WARN]  Redis bo'sh")
            return
        print(f"[INFO]  {len(members)} ta session:\n")
        for i, m in enumerate(members, 1):
            d = json.loads(m)
            sess    = d.get("session", "?")
            number  = d.get("number")  or "-"
            user_id = d.get("user_id") or "-"
            device  = d.get("device")  or "-"
            print(
                f"  {i:3d}. {number:<15s}  uid={user_id:<12s}"
                f"  app_id={d.get('app_id'):<8}  device={device}"
                f"  | {sess[:20]}..."
            )
        return

    # Tozalash
    if args.clear:
        old = r.scard(args.key)
        r.delete(args.key)
        print(f"[INFO]  {old} ta eski session o'chirildi")

    # Fayllarni o'qib yuklash
    sessions = load_json_files(args.dir)
    if not sessions:
        print("[WARN]  Yuklash uchun session yo'q")
        return

    n = push_sessions(r, args.key, sessions)
    total = r.scard(args.key)
    print(f"[OK]    {n} ta session yuklandi  |  Redis da jami: {total}")


if __name__ == "__main__":
    main()
