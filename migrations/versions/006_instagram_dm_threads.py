"""Remember which Discord thread relays which Instagram conversation, and how far Dobby has read.

Mapping and cursor metadata only: the Instagram conversation ID, the other participant's
Instagram-scoped ID (needed to reply), the Discord thread, open/closed, and the ID/time of the
newest message Dobby has seen. Message text is never stored; it lives in Instagram and Discord.
``status`` is free text: open (a thread is waiting for an answer) or closed.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "instagram_dm_threads",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("guild_id", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("participant_id", sa.Text),
        sa.Column("discord_channel_id", sa.Text),
        sa.Column("discord_thread_id", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="closed"),
        sa.Column("last_message_id", sa.Text),
        sa.Column("last_message_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("guild_id", "conversation_id", name="uq_instagram_dm_conversation"),
    )
    op.create_index("ix_instagram_dm_threads_thread", "instagram_dm_threads", ["discord_thread_id"])


def downgrade():
    op.drop_table("instagram_dm_threads")
