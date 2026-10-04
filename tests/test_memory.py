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
    {"discord_id": "1", "display_name": "Maya Chen", "calendar_email": "maya@uw.edu"},
    {"discord_id": "2", "display_name": "Leonard Park", "calendar_email": "leo@uw.edu"},
]


def person(name, email=None, discord_id="9"):
    return {"discord_id": discord_id, "display_name": name, "calendar_email": email}


# ---------------------------------------------------------------------------
# find_user_by_discord_id / match_user_by_name
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


def test_match_user_by_name_exact_full_name_ignores_case_and_spacing():
    from bot.memory import match_user_by_name

    async def run():
        match = await match_user_by_name(FakeSession(rows=PEOPLE), "  maya   CHEN ")
        assert match.user == PEOPLE[0] and not match.ambiguous and match.candidates == ()

    asyncio.run(run())


def test_match_user_by_name_unique_first_name_matches_even_without_an_email():
    from bot.memory import match_user_by_name

    rows = PEOPLE + [person("Raj Mehta")]

    async def run():
        assert (await match_user_by_name(FakeSession(rows=rows), "Leonard")).user == PEOPLE[1]
        assert (await match_user_by_name(FakeSession(rows=rows), "raj")).user == rows[2]

    asyncio.run(run())


def test_match_user_by_name_counts_people_without_an_email_when_checking_for_ties():
    from bot.memory import match_user_by_name

    # Only Maya Chen has an email, but "Maya" still names two people: never pick the emailed one.
    rows = PEOPLE + [person("Maya Ortiz")]

    async def run():
        match = await match_user_by_name(FakeSession(rows=rows), "Maya")
        assert match.user is None and match.ambiguous
        assert match.candidates == ("Maya Chen", "Maya Ortiz")

    asyncio.run(run())


def test_match_user_by_name_identical_names_are_ambiguous_and_listed_once():
    from bot.memory import match_user_by_name

    rows = [person("Sam Lee", "a@uw.edu", "7"), person("Sam Lee", "b@uw.edu", "8")]

    async def run():
        match = await match_user_by_name(FakeSession(rows=rows), "Sam Lee")
        assert match.user is None and match.ambiguous and match.candidates == ("Sam Lee",)

    asyncio.run(run())


def test_match_user_by_name_a_squatted_first_name_cannot_win_over_the_real_person():
    from bot.memory import match_user_by_name

    # Someone sets their display name to "Leonard"; the real Leonard Park is still a first-name fit.
    rows = [person("Leonard", "mallory@example.com", "666"), PEOPLE[1]]

    async def run():
        match = await match_user_by_name(FakeSession(rows=rows), "Leonard")
        assert match.user is None and match.ambiguous
        assert match.candidates == ("Leonard", "Leonard Park")

    asyncio.run(run())


def test_match_user_by_name_near_misses_are_suggestions_never_matches():
    from bot.memory import match_user_by_name

    rows = PEOPLE + [person("Alex Kimura", "alex@uw.edu")]

    async def run():
        close = await match_user_by_name(FakeSession(rows=rows), "Maya Chan")
        assert close.user is None and not close.ambiguous and close.candidates == ("Maya Chen",)
        # "Alex Kim" is a prefix of "Alex Kimura", not the same person.
        prefix = await match_user_by_name(FakeSession(rows=rows), "Alex Kim")
        assert prefix.user is None and prefix.candidates == ("Alex Kimura",)
        # A misspelled first name still suggests the person.
        assert (await match_user_by_name(FakeSession(rows=rows), "Leonrd")).candidates == ("Leonard Park",)

    asyncio.run(run())


def test_match_user_by_name_nothing_for_empty_or_unknown():
    from bot.memory import NameMatch, match_user_by_name

    async def run():
        assert await match_user_by_name(FakeSession(rows=PEOPLE), "") == NameMatch()
        assert await match_user_by_name(FakeSession(rows=PEOPLE), "Zebediah") == NameMatch()
        assert await match_user_by_name(FakeSession(rows=[]), "Maya") == NameMatch()

    asyncio.run(run())


def test_fitting_users_is_the_one_rule_for_lookups_and_late_invitations():
    from bot.memory import fitting_users

    rows = PEOPLE + [person("Straße Weiß")]
    assert fitting_users("@LEONARD", rows) == [PEOPLE[1]]  # a leading @ and case are ignored
    assert fitting_users("  maya   chen ", rows) == [PEOPLE[0]]
    assert fitting_users("strasse", rows) == [rows[2]]  # casefolded, as stored name keys are
    assert fitting_users("park", rows) == [] and fitting_users("", rows) == []
    with_ortiz = rows + [person("Maya Ortiz")]
    assert fitting_users("maya", with_ortiz) == [PEOPLE[0], with_ortiz[3]]


# ---------------------------------------------------------------------------
# display_names_for_emails
# ---------------------------------------------------------------------------


def test_display_names_for_emails_maps_lowercase_addresses_and_keeps_the_first_name():
    from bot.memory import display_names_for_emails

    rows = [
        {"email": "maya@uw.edu", "display_name": "Maya Chen"},
        {"email": "maya@uw.edu", "display_name": "Maya C."},
    ]

    async def run():
        session = FakeSession(rows=rows)
        assert await display_names_for_emails(session, ["Maya@UW.edu", None, "maya@uw.edu"]) == {
            "maya@uw.edu": "Maya Chen"
        }
        _, params = session.executed[0]
        assert params == {"emails": ["maya@uw.edu"]}
        empty = FakeSession()
        assert await display_names_for_emails(empty, []) == {} and not empty.executed

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
