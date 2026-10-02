"""Dashboard ORM models against real Postgres — UUID/ARRAY/JSONB, constraints."""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from .conftest import run_db


def test_user_session_cascade_delete(migrated_db):
    from dashboard.auth import make_token, session_expiry
    from dashboard.models import Session, User

    email = f"{uuid.uuid4().hex}@uw.edu"

    async def check(session):
        user = User(uw_email=email, display_name="Cascade Test", role="student")
        session.add(user)
        await session.flush()
        session.add(
            Session(
                user_id=user.id,
                token=make_token(str(user.id)),
                provider="local",
                expires_at=session_expiry(),
            )
        )
        await session.commit()

        await session.delete(user)
        await session.commit()

        count = await session.execute(
            sa.text("SELECT count(*) FROM sessions WHERE user_id = :u"),
            {"u": str(user.id)},
        )
        assert count.scalar_one() == 0

    run_db(check)


def test_integration_unique_per_user_and_provider(migrated_db):
    from dashboard.models import Integration, User

    email = f"{uuid.uuid4().hex}@uw.edu"

    async def check(session):
        user = User(uw_email=email, display_name="Dup Test", role="student")
        session.add(user)
        await session.flush()

        session.add(Integration(user_id=user.id, provider="google", composio_entity_id="e1"))
        await session.commit()

        session.add(Integration(user_id=user.id, provider="google", composio_entity_id="e2"))
        with pytest.raises(IntegrityError):
            await session.commit()

    run_db(check)


def test_agent_action_jsonb_round_trip(migrated_db):
    from sqlalchemy import select

    from dashboard.models import AgentAction

    guild = uuid.uuid4().hex

    async def check(session):
        session.add(
            AgentAction(
                guild_id=guild,
                tool="GOOGLECALENDAR_CREATE_EVENT",
                input={"summary": "Sync", "nested": {"k": [1, 2]}},
                output={"success": True},
                status="ok",
                duration_ms=42,
            )
        )
        await session.commit()

        row = (await session.execute(select(AgentAction).where(AgentAction.guild_id == guild))).scalar_one()
        assert row.input == {"summary": "Sync", "nested": {"k": [1, 2]}}
        assert row.output == {"success": True}

    run_db(check)


def test_guild_settings_array_round_trip(migrated_db):
    from sqlalchemy import select

    from dashboard.models import GuildSettings

    guild = uuid.uuid4().hex

    async def check(session):
        session.add(
            GuildSettings(
                guild_id=guild,
                timezone="UTC",
                model="gemini-test",
                allowed_role_ids=["1", "2"],
                admin_role_ids=[],
                allowed_channel_ids=["9"],
                mention_channel_ids=[],
                context_limit=5,
            )
        )
        await session.commit()

        row = (
            await session.execute(select(GuildSettings).where(GuildSettings.guild_id == guild))
        ).scalar_one()
        assert row.allowed_role_ids == ["1", "2"]
        assert row.allowed_channel_ids == ["9"]

    run_db(check)
