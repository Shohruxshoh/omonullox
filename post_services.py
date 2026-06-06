import asyncio
import re
from datetime import datetime, timezone
from telethon import TelegramClient, functions, errors, types
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetSponsoredPeersRequest
from telethon.tl.functions.messages import ViewSponsoredMessageRequest

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
        # proxy=build_proxy(session_data.get("proxy")),
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


# ─── SPONSORED SEARCH ────────────────────────────────────────────────────────

def sponsored_query_variants(keyword: str) -> list[str]:
    """Keyword uchun Telegram qidiruvida sinab ko'riladigan variantlarni qaytaradi."""
    normalized = " ".join(keyword.replace("_", " ").split())
    words = normalized.split()
    variants = [normalized]

    if len(words) > 1:
        variants.append("".join(words))
        if not words[-1].casefold().endswith("i"):
            possessive_words = [*words[:-1], f"{words[-1]}i"]
            variants.extend([" ".join(possessive_words), "".join(possessive_words)])
    elif words:
        compact = words[0]
        variants.extend(
            f"{compact[:index]} {compact[index:]}"
            for index in range(1, len(compact))
        )

    return list(dict.fromkeys(variant for variant in variants if variant))[:20]


def _sponsored_peer_key(peer) -> tuple[str, int] | None:
    if isinstance(peer, types.PeerChannel):
        return "channel", peer.channel_id
    if isinstance(peer, types.PeerChat):
        return "chat", peer.chat_id
    if isinstance(peer, types.PeerUser):
        return "user", peer.user_id
    return None


def _sponsored_entity_key(entity) -> tuple[str, int] | None:
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        return "channel", entity.id
    if isinstance(entity, (types.Chat, types.ChatForbidden)):
        return "chat", entity.id
    if isinstance(entity, (types.User, types.UserEmpty)):
        return "user", entity.id
    return None


def _sponsored_entity_row(
    keyword: str,
    query_used: str,
    entity,
    sponsored,
    account: str,
    round_number: int,
) -> dict:
    peer_type, peer_id = _sponsored_entity_key(entity) or ("other", 0)
    name = (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or "No name"
    )
    username = getattr(entity, "username", None)
    is_channel = isinstance(entity, types.Channel) and getattr(entity, "broadcast", False)
    is_bot = isinstance(entity, types.User) and getattr(entity, "bot", False)

    return {
        "keyword": keyword,
        "query_used": query_used,
        "account": account,
        "round": round_number,
        "seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entity_type": peer_type,
        "entity_id": peer_id,
        "name": name,
        "username": username or "",
        "link": f"https://t.me/{username}" if username else "",
        "type": "channel" if is_channel else "bot" if is_bot else "other",
        "source": "sponsored",
        "sponsor_info": getattr(sponsored, "sponsor_info", "") or "",
        "additional_info": getattr(sponsored, "additional_info", "") or "",
    }


async def find_sponsored_peers(
    session_data: dict,
    keyword: str,
    round_number: int,
    target_username: str | None = None,
) -> dict:
    """Bitta session orqali Telegram qidiruvidagi sponsored natijalarni oladi."""
    client = create_client(session_data)
    views_sent = 0
    views_failed = 0
    view_errors: list[str] = []
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {
                "status": "auth",
                "account": "",
                "rows": [],
                "views_sent": views_sent,
                "views_failed": views_failed,
                "view_errors": view_errors,
            }

        me = await client.get_me()
        account = getattr(me, "username", None) or str(getattr(me, "id", "unknown"))
        queries = [keyword.strip()]
        rows: list[dict] = []

        for query_used in queries:
            result = await client(GetSponsoredPeersRequest(q=query_used))
            entities = {
                _sponsored_entity_key(entity): entity
                for entity in [
                    *getattr(result, "chats", []),
                    *getattr(result, "users", []),
                ]
            }

            for sponsored in getattr(result, "peers", []):
                entity = entities.get(_sponsored_peer_key(sponsored.peer))
                if entity:
                    row = _sponsored_entity_row(
                        keyword,
                        query_used,
                        entity,
                        sponsored,
                        account,
                        round_number,
                    )
                    is_target = (
                        bool(target_username)
                        and row["username"].casefold() == target_username.casefold()
                    )
                    row["target_match"] = is_target
                    row["view_sent"] = False
                    row["view_error"] = ""

                    if target_username and not is_target:
                        random_id = getattr(sponsored, "random_id", None)
                        if not random_id:
                            views_failed += 1
                            row["view_error"] = "Sponsored peer random_id qaytarmadi"
                            view_errors.append(row["view_error"])
                        else:
                            try:
                                await client(ViewSponsoredMessageRequest(random_id=random_id))
                                views_sent += 1
                                row["view_sent"] = True
                            except (
                                errors.FloodWaitError,
                                errors.UserDeactivatedBanError,
                                errors.AuthKeyError,
                                errors.AuthKeyUnregisteredError,
                                errors.SessionRevokedError,
                                errors.PhoneNumberBannedError,
                            ):
                                raise
                            except Exception as e:
                                views_failed += 1
                                row["view_error"] = f"{type(e).__name__}: {e}"[:300]
                                view_errors.append(row["view_error"])

                    rows.append(row)

        return {
            "status": "ok",
            "account": account,
            "rows": rows,
            "views_sent": views_sent,
            "views_failed": views_failed,
            "view_errors": view_errors,
        }

    except errors.FloodWaitError as e:
        return {
            "status": f"flood:{e.seconds}",
            "account": "",
            "rows": [],
            "views_sent": views_sent,
            "views_failed": views_failed,
            "view_errors": view_errors,
        }

    except errors.UserDeactivatedBanError:
        return {
            "status": "banned",
            "account": "",
            "rows": [],
            "views_sent": views_sent,
            "views_failed": views_failed,
            "view_errors": view_errors,
        }

    except (errors.AuthKeyError, errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError, errors.PhoneNumberBannedError):
        return {
            "status": "auth",
            "account": "",
            "rows": [],
            "views_sent": views_sent,
            "views_failed": views_failed,
            "view_errors": view_errors,
        }

    except Exception as e:
        return {
            "status": "skip",
            "account": "",
            "rows": [],
            "views_sent": views_sent,
            "views_failed": views_failed,
            "view_errors": view_errors,
            "error": f"{type(e).__name__}: {e}"[:300],
        }

    finally:
        await client.disconnect()


# ─── SERVICE MAP ─────────────────────────────────────────────────────────────

SERVICE_MAP = {
    "views":     send_views_to_post,
    "reactions": send_reactions_to_post,
    "shares":    send_shares_to_post,
}
