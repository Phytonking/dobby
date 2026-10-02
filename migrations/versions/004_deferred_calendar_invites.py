"""Remember confirmed invitations waiting for an email address (not chat history)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("calendar_email_updated_at", sa.TIMESTAMP(timezone=True)))
    op.execute("UPDATE users SET calendar_email_updated_at = now() WHERE calendar_email IS NOT NULL")
    op.create_table(
        "calendar_invites",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("guild_id", sa.Text, nullable=False),
        sa.Column("channel_id", sa.Text, nullable=False),
        sa.Column("requester_id", sa.Text, nullable=False),
        sa.Column("calendar_id", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text, nullable=False),
        sa.Column("name_key", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("discord_id", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "guild_id", "calendar_id", "event_id", "name_key", name="uq_calendar_invite_target"
        ),
    )
    op.create_index("ix_calendar_invites_pending", "calendar_invites", ["guild_id", "channel_id", "status"])


def downgrade():
    op.drop_table("calendar_invites")
    op.drop_column("users", "calendar_email_updated_at")
