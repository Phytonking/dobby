"""initial schema

Revision ID: 001
Create Date: 2026-09-15
"""

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("uw_email", sa.Text, unique=True),
        sa.Column("discord_id", sa.Text, unique=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="student"),
        sa.Column("added_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "sessions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token", sa.Text, nullable=False, unique=True),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "integrations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("composio_entity_id", sa.Text, nullable=False),
        sa.Column("connected_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_integrations_user_provider", "integrations", ["user_id", "provider"])

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
        "user_facts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_user_facts_user_key", "user_facts", ["user_id", "key"])

    op.create_table(
        "agent_actions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("discord_id", sa.Text),
        sa.Column("guild_id", sa.Text),
        sa.Column("channel_id", sa.Text),
        sa.Column("tool", sa.Text),
        sa.Column("input", JSONB),
        sa.Column("output", JSONB),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_actions_created", "agent_actions", ["created_at"])

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


def downgrade():
    op.drop_table("contacts")
    op.drop_table("guild_settings")
    op.drop_table("agent_actions")
    op.drop_table("user_facts")
    op.drop_table("conversation_history")
    op.drop_table("integrations")
    op.drop_table("sessions")
    op.drop_table("users")
