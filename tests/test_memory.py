"""Tests for bot/memory.py — Postgres lookups for people, emails and the audit trail.

Uses a stub session that matches SQLAlchemy's async execute/mappings API.
"""

import asyncio


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
    def __init__(self, rows, rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self):
        return FakeMappings(self._rows)


class FakeSession:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self._rowcount = rowcount
        self.executed = []
        self.committed = False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return FakeResult(self._rows, self._rowcount)

    async def commit(self):
        self.committed = True


PEOPLE = [
    {"display_name": "Maya Chen", "calendar_email": "maya@uw.edu"},
    {"display_name": "Leonard Park", "calendar_email": "leo@uw.edu"},
]


# ---------------------------------------------------------------------------
# find_user_by_discord_id / find_user_by_name
# ---------------------------------------------------------------------------


def test_find_user_by_discord_id_returns_row_or_none():
    from bot.memory import find_user_by_discord_id

    async def run():
        assert await find_user_by_discord_id(FakeSession(rows=[]), "1") is None
        session = FakeSession(rows=[PEOPLE[0]])
        assert await find_user_by_discord_id(session, 1) == PEOPLE[0]
        _, params = session.executed[0]
        assert params == {"d": "1"}  # IDs are compared as text

    asyncio.run(run())


def test_find_user_by_name_exact_match():
    from bot.memory import find_user_by_name

    async def run():
        assert await find_user_by_name(FakeSession(rows=PEOPLE), "  maya   CHEN ") == PEOPLE[0]

    asyncio.run(run())


def test_find_user_by_name_unambiguous_first_name():
    from bot.memory import find_user_by_name

    async def run():
        assert await find_user_by_name(FakeSession(rows=PEOPLE), "Leonard") == PEOPLE[1]

    asyncio.run(run())


def test_find_user_by_name_ambiguous_first_name_falls_back_to_fuzzy():
    from bot.memory import find_user_by_name

    rows = PEOPLE + [{"display_name": "Maya Ortiz", "calendar_email": "mo@uw.edu"}]

    async def run():
        # Two Mayas: a bare first name is ambiguous, so no guess is made.
        assert await find_user_by_name(FakeSession(rows=rows), "Maya") is None
        # A close misspelling of a full name still resolves.
        assert await find_user_by_name(FakeSession(rows=rows), "Maya Chan") == PEOPLE[0]

    asyncio.run(run())


def test_find_user_by_name_none_for_empty_or_unknown():
    from bot.memory import find_user_by_name

    async def run():
        assert await find_user_by_name(FakeSession(rows=PEOPLE), "") is None
        assert await find_user_by_name(FakeSession(rows=PEOPLE), "Zebediah") is None
        assert await find_user_by_name(FakeSession(rows=[]), "Maya") is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# set_calendar_email
# ---------------------------------------------------------------------------


def test_set_calendar_email_reports_rows_changed():
    from bot.memory import set_calendar_email

    async def run():
        session = FakeSession(rowcount=1)
        assert await set_calendar_email(session, 7, "maya@uw.edu") == 1
        stmt, params = session.executed[0]
        assert "UPDATE users SET calendar_email" in stmt
        assert params == {"e": "maya@uw.edu", "d": "7"}
        assert await set_calendar_email(FakeSession(rowcount=0), 7, None) == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# record_action
# ---------------------------------------------------------------------------


def test_record_action_inserts_audit_row():
    from bot.memory import record_action

    async def run():
        session = FakeSession()
        await record_action(
            session,
            discord_id="1",
            guild_id="10",
            channel_id="40",
            tool="GOOGLECALENDAR_CREATE_EVENT",
            status="ok",
            duration_ms=123,
        )
        stmt, params = session.executed[0]
        assert "INSERT INTO agent_actions" in stmt
        assert "SELECT id FROM users WHERE discord_id" in stmt
        assert params == {
            "d": "1",
            "g": "10",
            "c": "40",
            "tool": "GOOGLECALENDAR_CREATE_EVENT",
            "status": "ok",
            "ms": 123,
        }
        # Metadata only: no tool arguments or results reach the database.
        assert "input" not in stmt and "output" not in stmt

    asyncio.run(run())
