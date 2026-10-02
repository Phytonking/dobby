"""merge contacts into users, drop conversation storage and guild settings, service-owned integrations

Revision ID: 002
Revises: 001
Create Date: 2026-09-17
"""

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


def upgrade():
    # 1. Each user may carry the address Dobby invites to meetings.
    op.add_column("users", sa.Column("calendar_email", sa.Text, nullable=True))

    # 2. Fold contacts into users: fill a matching user's email, otherwise add a directory-only user.
    op.execute(
        """
        UPDATE users u
        SET calendar_email = c.email
        FROM contacts c
        WHERE lower(u.display_name) = c.name_key AND u.calendar_email IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO users (display_name, calendar_email, role)
        SELECT c.display_name, c.email, 'student'
        FROM contacts c
        WHERE NOT EXISTS (
            SELECT 1 FROM users u WHERE lower(u.display_name) = c.name_key
        )
        """
    )

    # 3. Nothing conversational is stored any more; context comes live from Discord.
    op.drop_table("contacts")
    op.drop_table("conversation_history")
    op.drop_table("user_facts")

    # 4. One Composio entity owns the service accounts, so integrations are per provider.
    op.execute("DELETE FROM integrations")
    op.drop_constraint("uq_integrations_user_provider", "integrations", type_="unique")
    op.drop_column("integrations", "user_id")
    op.add_column(
        "integrations",
        sa.Column("connected_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_unique_constraint("uq_integrations_provider", "integrations", ["provider"])

    # 5. Dobby serves one guild and is configured by environment variables (TEAM_TIMEZONE,
    #    CONTEXT_MESSAGE_LIMIT, ALLOWED_*), so a per-guild settings table only duplicated .env.
    op.drop_table("guild_settings")

    # 6. The audit log keeps metadata only: no tool arguments or results, which carry chat content.
    op.drop_column("agent_actions", "input")
    op.drop_column("agent_actions", "output")


def downgrade():
    op.add_column("agent_actions", sa.Column("input", JSONB))
    op.add_column("agent_actions", sa.Column("output", JSONB))

    op.create_table(
        "guild_settings",
        sa.Column("guild_id", sa.Text, primary_key=True),
        sa.Column("timezone", sa.Text, nullable=False, server_default="America/Los_Angeles"),
        sa.Column("model", sa.Text, nullable=False, server_default="gemini-2.5-flash-lite"),
        sa.Column("allowed_role_ids", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("admin_role_ids", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("allowed_channel_ids", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("mention_channel_ids", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("context_limit", sa.Integer, nullable=False, server_default="12"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.execute("DELETE FROM integrations")
    op.drop_constraint("uq_integrations_provider", "integrations", type_="unique")
    op.drop_column("integrations", "connected_by")
    op.add_column(
        "integrations",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_unique_constraint("uq_integrations_user_provider", "integrations", ["user_id", "provider"])

    op.create_table(
        "user_facts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_user_facts_user_key", "user_facts", ["user_id", "key"])

    op.create_table(
        "conversation_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("guild_id", sa.Text, nullable=False),
        sa.Column("channel_id", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("tool_name", sa.Text),
        sa.Column("tool_input", JSONB),
        sa.Column("tool_result", JSONB),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_conversation_history_channel", "conversation_history", ["guild_id", "channel_id", "created_at"])

    op.create_table(
        "contacts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("guild_id", sa.Text, nullable=False),
        sa.Column("name_key", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("added_by", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_contacts_guild_name", "contacts", ["guild_id", "name_key"])

    op.drop_column("users", "calendar_email")
