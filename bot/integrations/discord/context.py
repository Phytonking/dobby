"""What Dobby reads before acting: recent human messages in the channel, and who was @mentioned.

Nothing here is stored. Context is fetched from Discord on every request and discarded.
"""

import logging
import re

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from ...memory import find_user_by_discord_id

log = logging.getLogger("context")

MENTION = re.compile(r"<@!?(\d+)>")
MAX_MESSAGE_CHARS = 500


async def gather_context(channel, *, limit: int, before=None) -> list[str]:
    """The last `limit` non-bot messages in `channel`, oldest first, one line each.

    `before` excludes the triggering message so the request is not repeated as context.
    Missing Read Message History permission yields no context rather than a failure.
    """
    lines = []
    try:
        # Bots may post often; scan a few times the limit to find enough human messages.
        async for message in channel.history(limit=limit * 4, before=before):
            if message.author.bot:
                continue
            lines.append(format_message(message))
            if len(lines) >= limit:
                break
    except discord.HTTPException as exc:
        log.warning("context_unavailable channel=%s http_status=%s", getattr(channel, "id", None), exc.status)
        return []
    lines.reverse()
    return lines


def format_message(message) -> str:
    stamp = message.created_at.strftime("%Y-%m-%d %H:%M")
    text = " ".join(str(message.clean_content).split())[:MAX_MESSAGE_CHARS]
    return f"[{stamp}] {message.author.display_name}: {text}"


async def resolve_mentions(session: AsyncSession, text: str) -> tuple[str, list[dict]]:
    """Swap `<@id>` tokens for `@Display Name` and return the registered people they name.

    Unknown IDs are left as they are so the model sees that someone was mentioned.
    """
    found = {}  # discord_id -> user row (or None), one lookup per ID
    for discord_id in MENTION.findall(text):
        if discord_id not in found:
            found[discord_id] = await find_user_by_discord_id(session, discord_id)

    def replace(match):
        user = found.get(match.group(1))
        return f"@{user['display_name']}" if user else match.group(0)

    people = [{**user, "discord_id": discord_id} for discord_id, user in found.items() if user]
    return MENTION.sub(replace, text), people
