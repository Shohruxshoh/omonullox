import json
import os

import redis.asyncio as redis
from cryptography.fernet import Fernet, InvalidToken


class RedisSessionManager:
    def __init__(self, url="redis://localhost:6379"):
        self.redis = redis.from_url(url, decode_responses=True)
        key = os.environ.get("SESSION_FERNET_KEY", "").strip()
        self._fernet = Fernet(key.encode()) if key else None

    def _encrypt_session(self, session_str: str) -> str:
        if self._fernet:
            return self._fernet.encrypt(session_str.encode()).decode()
        return session_str

    def _decrypt_session(self, value: str) -> str:
        if self._fernet:
            try:
                return self._fernet.decrypt(value.encode()).decode()
            except (InvalidToken, Exception):
                # Eski shifrlenmagan ma'lumot — as-is qaytaramiz
                return value
        return value

    async def get_all_sessions(self, key: str) -> list[dict]:
        raw = await self.redis.smembers(key)
        result = []
        for x in raw:
            d = json.loads(x)
            if "session" in d:
                d["session"] = self._decrypt_session(d["session"])
            result.append(d)
        return result

    async def add_session(self, key: str, session_data: dict) -> None:
        """Bitta sessionni shifirlab Redis ga qo'shadi."""
        data = dict(session_data)
        if "session" in data:
            data["session"] = self._encrypt_session(data["session"])
        await self.redis.sadd(key, json.dumps(data, ensure_ascii=False))

    async def add_sessions_bulk(self, key: str, sessions: list[dict]) -> None:
        """Sessionlar ro'yxatini shifirlab pipeline orqali Redis ga qo'shadi."""
        pipe = self.redis.pipeline()
        for s in sessions:
            data = dict(s)
            if "session" in data:
                data["session"] = self._encrypt_session(data["session"])
            pipe.sadd(key, json.dumps(data, ensure_ascii=False))
        await pipe.execute()

    async def remove_session(self, key: str, session_json: str):
        await self.redis.srem(key, session_json)

    async def remove_session_by_id(self, key: str, session_data: dict) -> bool:
        """Session dict bo'yicha Redis dan topib o'chiradi (uid/user_id/number orqali)."""
        session_id = (
            session_data.get("uid") or
            session_data.get("user_id") or
            session_data.get("number")
        )
        if not session_id:
            return False
        raw = await self.redis.smembers(key)
        for item in raw:
            try:
                parsed = json.loads(item)
                item_id = (
                    parsed.get("uid") or
                    parsed.get("user_id") or
                    parsed.get("number")
                )
                if str(item_id) == str(session_id):
                    await self.redis.srem(key, item)
                    return True
            except Exception:
                continue
        return False

    async def close(self):
        await self.redis.aclose()
