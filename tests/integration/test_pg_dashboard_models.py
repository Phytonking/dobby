"""Dashboard ORM models against real Postgres — UUIDs, cascades and unique constraints."""

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


def test_integration_is_one_shared_row_per_provider(migrated_db):
    from dashboard.models import Integration

    provider = f"svc_{uuid.uuid4().hex[:8]}"

    async def check(session):
        session.add(Integration(provider=provider, composio_entity_id="dobby"))
        await session.commit()

        session.add(Integration(provider=provider, composio_entity_id="dobby"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    run_db(check)


def test_deleting_the_connecting_admin_keeps_the_integration(migrated_db):
    from dashboard.models import Integration, User

    provider = f"svc_{uuid.uuid4().hex[:8]}"

    async def check(session):
        admin = User(uw_email=f"{uuid.uuid4().hex}@uw.edu", display_name="Admin", role="admin")
        session.add(admin)
        await session.flush()
        session.add(Integration(provider=provider, composio_entity_id="dobby", connected_by=admin.id))
        await session.commit()

        await session.delete(admin)
        await session.commit()

        row = (
            await session.execute(
                sa.text("SELECT connected_by FROM integrations WHERE provider = :p"), {"p": provider}
            )
        ).one()
        assert row.connected_by is None

    run_db(check)


def test_local_username_is_unique(migrated_db):
    from dashboard.models import User

    username = f"admin_{uuid.uuid4().hex[:8]}"

    async def check(session):
        session.add(User(local_username=username, display_name="First", role="admin"))
        await session.commit()

        session.add(User(local_username=username, display_name="Second", role="admin"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    run_db(check)


def test_agent_action_round_trip(migrated_db):
    from dashboard.models import AgentAction

    guild = uuid.uuid4().hex

    async def check(session):
        session.add(
            AgentAction(
                discord_id="1",
                guild_id=guild,
                channel_id="c",
                tool="NOTION_SEARCH",
                status="ok",
                duration_ms=42,
            )
        )
        await session.commit()

        row = (
            await session.execute(sa.select(AgentAction).where(AgentAction.guild_id == guild))
        ).scalar_one()
        assert (row.tool, row.status, row.duration_ms) == ("NOTION_SEARCH", "ok", 42)
        assert row.created_at is not None

    run_db(check)
