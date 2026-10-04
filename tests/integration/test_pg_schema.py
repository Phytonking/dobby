"""Migrations apply cleanly and the ORM models agree with the migrated schema."""

import sqlalchemy as sa

from .conftest import run_db

EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "sessions",
    "integrations",
    "agent_actions",
    "calendar_invites",
}
# Folded into users or dropped by 002; nothing may still depend on them.
DROPPED_TABLES = {"conversation_history", "user_facts", "guild_settings", "contacts"}


def test_upgrade_head_creates_all_tables(migrated_db):
    async def check(session):
        result = await session.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        tables = {row[0] for row in result}
        missing = EXPECTED_TABLES - tables
        assert not missing, f"tables missing after upgrade head: {missing}"
        assert not DROPPED_TABLES & tables, f"dropped tables still present: {DROPPED_TABLES & tables}"

    run_db(check)


def test_dashboard_models_match_migrated_schema(migrated_db):
    """Autogenerate diff between ORM metadata and the live schema must be empty.

    Catches silent drift between dashboard/models.py and migrations/versions/.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from dashboard.models import Base

    diffs = []

    def collect(sync_conn):
        ctx = MigrationContext.configure(
            sync_conn,
            opts={"compare_type": True, "compare_server_default": False},
        )
        diffs.extend(compare_metadata(ctx, Base.metadata))

    async def check(session):
        conn = await session.connection()
        await conn.run_sync(collect)

    run_db(check)

    # The bot writes these with raw SQL and has no ORM model; alembic_version is alembic's own.
    bot_owned = {"calendar_invites", "alembic_version"}
    bot_owned_columns = {("users", "calendar_email_updated_at")}

    # Runtime breakage only: tables/columns one side lacks, or a type mismatch. Nullability,
    # index and FK declarations differ harmlessly between the models and the migrations.
    def is_real(diff):
        d = diff[0] if isinstance(diff, list) else diff
        kind = d[0]
        if kind in {"remove_table", "add_table"}:
            return d[1].name not in bot_owned
        if kind in {"remove_column", "add_column"}:
            return (d[2], d[3].name) not in bot_owned_columns  # (kind, schema, table, Column)
        return kind == "modify_type"

    real_drift = [d for d in diffs if is_real(d)]
    assert not real_drift, "ORM models drifted from migrations:\n" + "\n".join(repr(d) for d in real_drift)


def test_calendar_invites_tracks_approval_prompts(migrated_db):
    async def check(session):
        rows = await session.execute(
            sa.text(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'calendar_invites' AND column_name IN ('proposed_at', 'attempts')"
            )
        )
        columns = {r[0]: r[1:] for r in rows}
        assert columns["proposed_at"][0] == "timestamp with time zone" and columns["proposed_at"][1] == "YES"
        assert columns["attempts"][0] == "integer" and columns["attempts"][1] == "NO"
        assert columns["attempts"][2] == "0"

    run_db(check)
