"""Late invitations ask the requester before anyone is added: remember when and how often.

``calendar_invites.status`` is free text: pending, proposed (an approval prompt is live), completed,
declined, expired. ``proposed_at`` times prompts and retries; ``attempts`` caps them.
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("calendar_invites", sa.Column("proposed_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("calendar_invites", sa.Column("attempts", sa.Integer, nullable=False, server_default="0"))


def downgrade():
    op.drop_column("calendar_invites", "attempts")
    op.drop_column("calendar_invites", "proposed_at")
