"""Tests for bot/memory.py — Postgres-backed memory helpers.

Uses a stub session that matches SQLAlchemy's async execute/mappings API.
"""

import asyncio
import json


# ---------------------------------------------------------------------------
# Session/result stubs matching SQLAlchemy async execute() API
# ---------------------------------------------------------------------------

class FakeMappings:
    def __init__(self, rows):
        self._rows = rows  # list of dicts

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return FakeMappings(self._rows)


class FakeSession:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed = []
        self.committed = False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return FakeResult(self._rows)

    async def commit(self):
        self.committed = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_history_returns_empty_when_no_rows():
    from bot.memory import load_history

    async def run():
        session = FakeSession(rows=[])
        result = await load_history(session, "guild1", "chan1")
        assert result == []

    asyncio.run(run())


def test_load_history_returns_rows_reversed():
    from bot.memory import load_history

    async def run():
        # memory.py returns DESC then reverses → oldest first
        rows = [
            {"role": "model", "content": "hi", "tool_name": None, "tool_input": None, "tool_result": None},
            {"role": "user", "content": "hello", "tool_name": None, "tool_input": None, "tool_result": None},
        ]
        session = FakeSession(rows=rows)
        result = await load_history(session, "guild1", "chan1")
        assert len(result) == 2
        # reversed: user first, then model
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "model"

    asyncio.run(run())


def test_append_turn_executes_insert():
    from bot.memory import append_turn

    async def run():
        session = FakeSession()
        await append_turn(session, "guild1", "chan1", role="user", content="test")
        assert len(session.executed) == 1
        stmt, params = session.executed[0]
        assert "INSERT" in stmt
        assert params["g"] == "guild1"
        assert params["c"] == "chan1"
        assert params["role"] == "user"
        assert params["content"] == "test"

    asyncio.run(run())


def test_append_turn_serializes_tool_fields():
    from bot.memory import append_turn

    async def run():
        session = FakeSession()
        await append_turn(
            session,
            "guild1",
            "chan1",
            role="tool",
            tool_name="SOME_TOOL",
            tool_input={"x": 1},
            tool_result={"success": True},
        )
        _, params = session.executed[0]
        assert params["role"] == "tool"
        assert params["tool_name"] == "SOME_TOOL"
        # tool_input/result are JSON strings (for ::jsonb cast)
        assert json.loads(params["tool_input"]) == {"x": 1}
        assert json.loads(params["tool_result"]) == {"success": True}

    asyncio.run(run())


def test_save_contact_executes_upsert():
    from bot.memory import save_contact

    async def run():
        session = FakeSession()
        await save_contact(session, "guild1", "Maya Chen", "maya@uw.edu", added_by="99")
        assert len(session.executed) == 1
        stmt, params = session.executed[0]
        assert "INSERT" in stmt
        assert params["nk"] == "maya chen"
        assert params["dn"] == "Maya Chen"
        assert params["email"] == "maya@uw.edu"
        assert params["g"] == "guild1"

    asyncio.run(run())


def test_save_contact_normalizes_name():
    from bot.memory import save_contact

    async def run():
        session = FakeSession()
        await save_contact(session, "guild1", "  MAYA   CHEN  ", "maya@uw.edu")
        _, params = session.executed[0]
        assert params["nk"] == "maya chen"
        assert params["dn"] == "MAYA CHEN"  # save_contact strips whitespace but preserves caller's casing

    asyncio.run(run())


def test_lookup_contact_returns_none_for_empty_db():
    from bot.memory import lookup_contact

    async def run():
        session = FakeSession(rows=[])
        result = await lookup_contact(session, "guild1", "Maya")
        assert result is None

    asyncio.run(run())


def test_lookup_contact_exact_match():
    from bot.memory import lookup_contact

    async def run():
        rows = [{"name_key": "maya chen", "display_name": "Maya Chen", "email": "maya@uw.edu"}]
        session = FakeSession(rows=rows)
        result = await lookup_contact(session, "guild1", "maya chen")
        assert result == "maya@uw.edu"

    asyncio.run(run())


def test_lookup_contact_fuzzy_first_name():
    from bot.memory import lookup_contact

    async def run():
        rows = [{"name_key": "maya chen", "display_name": "Maya Chen", "email": "maya@uw.edu"}]
        session = FakeSession(rows=rows)
        # "maya" has good overlap with "maya chen"
        result = await lookup_contact(session, "guild1", "Maya")
        assert result == "maya@uw.edu"

    asyncio.run(run())


def test_load_guild_settings_returns_none_when_absent():
    from bot.memory import load_guild_settings

    async def run():
        session = FakeSession(rows=[])
        result = await load_guild_settings(session, "guild1")
        assert result is None

    asyncio.run(run())


def test_load_guild_settings_returns_dict_when_present():
    from bot.memory import load_guild_settings

    async def run():
        row = {
            "guild_id": "guild1",
            "timezone": "America/Los_Angeles",
            "model": "gemini-test",
            "context_limit": 12,
            "allowed_role_ids": [],
            "admin_role_ids": [],
            "allowed_channel_ids": [],
            "mention_channel_ids": [],
        }
        session = FakeSession(rows=[row])
        result = await load_guild_settings(session, "guild1")
        assert result is not None
        assert result["timezone"] == "America/Los_Angeles"

    asyncio.run(run())
