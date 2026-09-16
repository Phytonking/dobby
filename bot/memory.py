from difflib import SequenceMatcher

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def load_history(session: AsyncSession, guild_id: str, channel_id: str, limit: int = 12) -> list[dict]:
    result = await session.execute(
        sa.text(
            "SELECT role, content, tool_name, tool_input, tool_result "
            "FROM conversation_history "
            "WHERE guild_id = :g AND channel_id = :c "
            "ORDER BY created_at DESC LIMIT :lim"
        ),
        {"g": guild_id, "c": channel_id, "lim": limit},
    )
    rows = result.mappings().all()
    return [dict(r) for r in reversed(rows)]


async def append_turn(
    session: AsyncSession,
    guild_id: str,
    channel_id: str,
    role: str,
    content: str | None = None,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    tool_result: dict | None = None,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO conversation_history (guild_id, channel_id, role, content, tool_name, tool_input, tool_result) "
            "VALUES (:g, :c, :role, :content, :tool_name, :tool_input::jsonb, :tool_result::jsonb)"
        ),
        {
            "g": guild_id,
            "c": channel_id,
            "role": role,
            "content": content,
            "tool_name": tool_name,
            "tool_input": __import__("json").dumps(tool_input) if tool_input is not None else None,
            "tool_result": __import__("json").dumps(tool_result) if tool_result is not None else None,
        },
    )


def _normalize(name: str) -> str:
    return " ".join(str(name).split()).lower()


async def lookup_contact(session: AsyncSession, guild_id: str, name: str) -> str | None:
    result = await session.execute(
        sa.text("SELECT name_key, display_name, email FROM contacts WHERE guild_id = :g"),
        {"g": guild_id},
    )
    rows = result.mappings().all()
    key = _normalize(name)
    # Exact match first
    for row in rows:
        if row["name_key"] == key:
            return row["email"]
    # Fuzzy match using SequenceMatcher (mirrors contacts.py logic)
    best_score, best_email = 0.0, None
    for row in rows:
        s = SequenceMatcher(None, key, row["name_key"]).ratio()
        if s > best_score:
            best_score, best_email = s, row["email"]
    return best_email if best_score >= 0.6 else None


async def save_contact(
    session: AsyncSession, guild_id: str, name: str, email: str, added_by: str | None = None
) -> None:
    display = " ".join(str(name).split())
    name_key = _normalize(display)
    await session.execute(
        sa.text(
            "INSERT INTO contacts (guild_id, name_key, display_name, email, added_by) "
            "VALUES (:g, :nk, :dn, :email, :ab) "
            "ON CONFLICT (guild_id, name_key) DO UPDATE SET display_name = EXCLUDED.display_name, email = EXCLUDED.email, added_by = EXCLUDED.added_by"
        ),
        {"g": guild_id, "nk": name_key, "dn": display, "email": email, "ab": added_by},
    )


async def list_contacts(session: AsyncSession, guild_id: str) -> list[dict]:
    result = await session.execute(
        sa.text(
            "SELECT display_name, email FROM contacts WHERE guild_id = :g ORDER BY display_name"
        ),
        {"g": guild_id},
    )
    return [dict(r) for r in result.mappings().all()]


async def load_guild_settings(session: AsyncSession, guild_id: str) -> dict | None:
    result = await session.execute(
        sa.text("SELECT * FROM guild_settings WHERE guild_id = :g"),
        {"g": guild_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None
