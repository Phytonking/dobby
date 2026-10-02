"""Upgrade actual Postgres schemas, never SQL text mocks."""

import os
import subprocess
import sys
import uuid

import asyncpg
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", ["001", "002"])
async def test_07_migration_image_preserves_supported_data(previous):
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    database = "regression_migration_" + uuid.uuid4().hex
    admin = await asyncpg.connect(url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    test_url = url.rsplit("/", 1)[0] + "/" + database
    connection = None
    try:

        def migrate(revision):
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "migrations/alembic.ini", "upgrade", revision],
                env=os.environ | {"DATABASE_URL": test_url},
                capture_output=True,
                text=True,
                timeout=45,
            )
            assert result.returncode == 0, result.stderr

        migrate(previous)
        connection = await asyncpg.connect(test_url)
        user_id = await connection.fetchval(
            "INSERT INTO users (display_name, uw_email, discord_id, role) VALUES ('Existing', 'existing@example.org', '42', 'admin') RETURNING id"
        )
        await connection.execute(
            "INSERT INTO sessions (user_id, token, provider, expires_at) VALUES ($1, 'old-token', 'google', now() + interval '1 day')",
            user_id,
        )
        if previous == "001":
            await connection.execute(
                "INSERT INTO contacts (guild_id, name_key, display_name, email) VALUES ('10', 'existing', 'Existing', 'calendar@example.org')"
            )
        else:
            await connection.execute(
                "UPDATE users SET calendar_email='calendar@example.org' WHERE id=$1", user_id
            )
            await connection.execute(
                "INSERT INTO integrations (provider, composio_entity_id, connected_by) VALUES ('notion', 'dobby', $1)",
                user_id,
            )
        migrate("head")
        migrate("head")
        row = await connection.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
        assert row["role"] == "admin" and row["calendar_email"] == "calendar@example.org"
        assert row["uw_email"] == "existing@example.org" and row["discord_id"] == "42"
        assert row["local_username"] is None
        assert row["calendar_email_updated_at"] is not None
        assert await connection.fetchval("SELECT to_regclass('calendar_invites')") is not None
        assert await connection.fetchval("SELECT count(*) FROM sessions WHERE token='old-token'") == 1
        if previous == "002":
            assert (
                await connection.fetchval(
                    "SELECT composio_entity_id FROM integrations WHERE provider='notion'"
                )
                == "dobby"
            )
        assert await connection.fetchval("SELECT to_regclass('conversation_history')") is None
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()
