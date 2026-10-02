"""Migrations apply cleanly and the ORM models agree with the migrated schema."""

import sqlalchemy as sa

from .conftest import run_db

EXPECTED_TABLES = {
    "alembic_version",
    "users",
    "sessions",
    "integrations",
    "conversation_history",
    "user_facts",
    "agent_actions",
    "guild_settings",
    "contacts",
}


def test_upgrade_head_creates_all_tables(migrated_db):
    async def check(session):
        result = await session.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        tables = {row[0] for row in result}
        missing = EXPECTED_TABLES - tables
        assert not missing, f"tables missing after upgrade head: {missing}"

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

    # conversation_history/user_facts are bot-owned (raw SQL, no ORM model);
    # alembic_version is alembic's own. Diffs on those tables are expected.
    bot_owned = {"conversation_history", "user_facts", "alembic_version"}

    def is_bot_owned(diff):
        d = diff[0] if isinstance(diff, list) else diff
        kind = d[0]
        if kind in {"remove_table", "add_table"}:
            return d[1].name in bot_owned
        if kind in {"remove_index", "add_index", "remove_constraint", "add_constraint"}:
            return d[1].table.name in bot_owned
        if kind.startswith("modify_"):
            return d[2] in bot_owned  # (kind, schema, table, column, ...)
        return False

    real_drift = [d for d in diffs if not is_bot_owned(d)]
    assert not real_drift, "ORM models drifted from migrations:\n" + "\n".join(repr(d) for d in real_drift)
