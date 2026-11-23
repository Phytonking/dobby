"""Dobby's Luma connection: OAuth tokens for Luma's MCP server (https://mcp.luma.com).

Luma has no Composio toolkit, so unlike the other services Dobby holds this connection itself.
One row at most (id is always 1): the admin who connected it, the OAuth client ID (a URL to
Dobby's client metadata document), the token endpoint, and the access/refresh tokens. The bot
refreshes and rotates the tokens in place; disconnecting on the dashboard revokes and deletes them.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "luma_connection",
        sa.Column("id", sa.SmallInteger, primary_key=True, server_default="1"),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("token_endpoint", sa.Text, nullable=False),
        sa.Column("revocation_endpoint", sa.Text),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("connected_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("connected_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("id = 1", name="ck_luma_connection_single_row"),
    )


def downgrade():
    op.drop_table("luma_connection")
