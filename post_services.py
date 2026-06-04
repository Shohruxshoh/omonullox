import asyncio
import re
from telethon import TelegramClient, functions, errors, types
from telethon.sessions import StringSession

SHARE_TARGET = "me"


def parse_post_link(post_link: str) -> tuple[str, int]:
    """
    Post linkini parse qiladi.
    https://t.me/mychannel/123         → ("mychannel", 123)
    https://t.me/c/1234567890/456      → (-1001234567890, 456)
    """
    # Private kanal: t.me/c/CHANNEL_ID/MSG_ID
    private = re.match(r"https?://t\.me/c/(\d+)/(\d+)", post_link)
    if private:
        channel_id = int("-100" + private.group(1))
        msg_id = int(private.group(2))
        return channel_id, msg_id

    # Public kanal: t.me/username/MSG_ID
    public = re.match(r"https?://t\.me/([^/]+)/(\d+)", post_link)
    if public:
        return public.group(1), int(public.group(2))

    raise ValueError(f"Noto'g'ri post link: {post_link}")


def build_proxy(proxy_str: str | None) -> dict | None:
    if not proxy_str:
        return None
    try:
        h, p, u, pw = proxy_str.split(":")
        return {
            "proxy_type": "socks5",
            "addr": h,
            "port": int(p),
            "username": u,
            "password": pw,
        }
    except Exception:
        return None


def create_client(session_data: dict) -> TelegramClient:
    return TelegramClient(
        StringSession(session_data["session"]),
        session_data["app_id"],
        session_data["app_hash"],
        proxy=build_proxy(session_data.get("proxy")),
        timeout=10,
        connection_retries=1,
    )


# ─── VIEWS ───────────────────────────────────────────────────────────────────

async def send_views_to_post(session_data: dict, channel, msg_id: int) -> str:
    """Bitta postga view yuboradi. Status qaytaradi: ok/flood:N/banned/auth/skip."""
    client = create_client(session_data)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "auth"

        entity = await client.get_input_entity(channel)
        await client(functions.messages.GetMessagesViewsRequest(
            peer=entity,
            id=[msg_id],
            increment=True
        ))
        return "ok"

    except errors.FloodWaitError as e:
        return f"flood:{e.seconds}"

    except errors.UserDeactivatedBanError:
        return "banned"

    except (errors.AuthKeyError, errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError, errors.PhoneNumberBannedError):
        return "auth"

    except Exception:
        return "skip"

    finally:
        await client.disconnect()


# ─── REACTIONS ────────────────────────────────────────────────────────────────

async def check_reaction_available(session_data: dict, channel, msg_id: int, emoji: str) -> str:
    """
    Checker account o'zi reaction yuborib tekshiradi.
    Muvaffaqiyatli bo'lsa "ok" qaytaradi — reaction yuborildi va ruxsat bor.
    Ruxsat bo'lmasa "reaction_not_allowed" qaytaradi.
    Qaytaradi: ok / reaction_not_allowed / flood:N / banned / auth / skip
    """
    client = create_client(session_data)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "auth"

        entity = await client.get_input_entity(channel)
        await client(functions.messages.SendReactionRequest(
            peer=entity,
            msg_id=msg_id,
            reaction=[types.ReactionEmoji(emoticon=emoji)]
        ))
        return "ok"

    except errors.FloodWaitError as e:
        return f"flood:{e.seconds}"

    except errors.UserDeactivatedBanError:
        return "banned"

    except (errors.AuthKeyError, errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError, errors.PhoneNumberBannedError):
        return "auth"

    except Exception as e:
        # ReactionInvalidError, ChatSendReactionsForbiddenError va boshqa reaction xatolari
        if "reaction" in type(e).__name__.lower() or "reaction" in str(e).lower():
            return "reaction_not_allowed"
        return "skip"

    finally:
        await client.disconnect()


async def send_reactions_to_post(session_data: dict, channel, msg_id: int, emoji: str) -> str:
    """Bitta postga berilgan emoji reaksiya yuboradi. Status qaytaradi."""
    client = create_client(session_data)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "auth"

        entity = await client.get_input_entity(channel)
        await client(functions.messages.SendReactionRequest(
            peer=entity,
            msg_id=msg_id,
            reaction=[types.ReactionEmoji(emoticon=emoji)]
        ))
        return "ok"

    except errors.FloodWaitError as e:
        return f"flood:{e.seconds}"

    except errors.UserDeactivatedBanError:
        return "banned"

    except (errors.AuthKeyError, errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError, errors.PhoneNumberBannedError):
        return "auth"

    except Exception:
        return "skip"

    finally:
        await client.disconnect()


# ─── SHARES ──────────────────────────────────────────────────────────────────

async def send_shares_to_post(session_data: dict, channel, msg_id: int) -> str:
    """Postni Saved Messages ga forward qilib, darhol o'chiradi. Status qaytaradi."""
    client = create_client(session_data)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return "auth"

        source = await client.get_input_entity(channel)
        target = await client.get_input_entity(SHARE_TARGET)

        forwarded = await client.forward_messages(target, msg_id, source)

        # Saved Messages to'lib qolmasin — darhol o'chirish
        if forwarded:
            ids = [m.id for m in (forwarded if isinstance(forwarded, list) else [forwarded])]
            await client.delete_messages(target, ids)

        return "ok"

    except errors.FloodWaitError as e:
        return f"flood:{e.seconds}"

    except errors.UserDeactivatedBanError:
        return "banned"

    except (errors.AuthKeyError, errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError, errors.PhoneNumberBannedError):
        return "auth"

    except Exception:
        return "skip"

    finally:
        await client.disconnect()


# ─── SERVICE MAP ─────────────────────────────────────────────────────────────

SERVICE_MAP = {
    "views":     send_views_to_post,
    "reactions": send_reactions_to_post,
    "shares":    send_shares_to_post,
}
