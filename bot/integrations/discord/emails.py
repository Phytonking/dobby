"""Capture explicit self-provided addresses, with the Discord author as the identity."""

import logging
import re

import discord

from ...db import SessionLocal
from ...memory import save_calendar_email
from ...models import valid_email
from ...voice import say

log = logging.getLogger("email")
ADDRESS = r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+"
SELF_EMAIL = re.compile(r"\bmy\s+(?:e-?mail)(?:\s+address)?\s*(?:is\s*|[:=]\s*)(<?" + ADDRESS + r">?)", re.I)
SELF_NAME = re.compile(r"\bmy name is\s+(.+?)(?:\s+and\s+|[,;]\s*)my\s+e-?mail", re.I)
NAMED_REPLY = re.compile(r"^([^:<>@]{1,100}):\s*(<?" + ADDRESS + r">?)\s*$")


def parse_email(text, display_name, *, allow_bare=False):
    if not isinstance(text, str) or "\n" in text or "`" in text or text.lstrip().startswith(">"):
        return None
    declaration = re.sub(
        r"^(?:(?:hey|hi|hello)[,!]?\s*)?(?:<@!?\d+>|@dobby)?[\s,!:.]*", "", text.strip(), flags=re.I
    )
    match = (
        SELF_EMAIL.search(declaration)
        if re.match(r"^(?:my\s|here(?:'s| is) my\s)", declaration, re.I)
        else None
    )
    named = NAMED_REPLY.match(text.strip()) if allow_bare else None
    address = match.group(1) if match else named.group(2) if named else text.strip() if allow_bare else ""
    address = address.rstrip(".,!;").strip("<>").lower()
    if not valid_email(address):
        return None
    name_match = SELF_NAME.search(text)
    name = name_match.group(1).strip() if name_match else named.group(1).strip() if named else display_name
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        return None
    return name, address


async def email_reply(bot, message):
    reference = getattr(message, "reference", None)
    message_id = getattr(reference, "message_id", None)
    if not isinstance(message_id, int):
        return False
    try:
        original = await message.channel.fetch_message(message_id)
    except discord.HTTPException:
        return False
    return original.author.id == bot.user.id and "email" in original.content.lower()


async def capture_email(bot, message, *, history=False):
    """Returns True only for a recognized self-email message (never interpret another person's address)."""
    if message.author.bot or not isinstance(message.content, str):
        return False
    parsed = parse_email(message.content, message.author.display_name)
    named = NAMED_REPLY.match(message.content.strip())
    if parsed is None and (valid_email(message.content.strip().strip("<>")) or named):
        if await email_reply(bot, message):
            parsed = parse_email(message.content, message.author.display_name, allow_bare=True)
    if parsed is None:
        return False
    if not await bot.member_allowed(message.author.id, message.channel.id):
        return False
    name, address = parsed
    try:
        async with SessionLocal() as session:
            changed = await save_calendar_email(
                session,
                str(message.author.id),
                address,
                name,
                observed_at=message.created_at,
                replace_name=bool(SELF_NAME.search(message.content) or named),
            )
            await session.commit()
        if not history:
            text = say("email_saved") if changed else "Dobby already has a newer email update for you."
            await message.reply(text, mention_author=False)
            invited = await bot.reconcile_calendar_invites()
            if invited:
                await message.reply(
                    f"Dobby has completed {invited} waiting calendar invitation(s)!", mention_author=False
                )
    except Exception as exc:
        log.warning("email_capture_failed type=%s", type(exc).__name__)
        if not history:
            await message.reply(say("generic_failure"), mention_author=False)
    return True


async def recover_recent_emails(bot, channel, *, before=None):
    """Read only the same bounded human-message window as the agent's context. Store no chat text."""
    if not bot.config.context_limit or not bot.config.mentionable(channel.id):
        return
    if bot.config.channels and channel.id not in bot.config.channels:
        return
    count = 0
    try:
        async for message in channel.history(limit=bot.config.context_limit * 4, before=before):
            if message.author.bot:
                continue
            await capture_email(bot, message, history=True)
            count += 1
            if count >= bot.config.context_limit:
                break
    except discord.HTTPException as exc:
        log.warning("email_history_unavailable status=%s", exc.status)
