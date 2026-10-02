"""Optional local username/password credentials."""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("local_username", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_users_local_username", "users", ["local_username"])


def downgrade():
    op.drop_constraint("uq_users_local_username", "users", type_="unique")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "local_username")
