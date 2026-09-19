"""add password_hash to users for local auth

Revision ID: 002
Revises: 001
Create Date: 2026-09-19
"""

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("users", sa.Column("password_hash", sa.Text, nullable=True))


def downgrade():
    op.drop_column("users", "password_hash")
