"""add session last_used_at

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_sessions",
        sa.Column("last_used_at", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "ix_telegram_sessions_last_used_at",
        "telegram_sessions",
        ["last_used_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_sessions_last_used_at", table_name="telegram_sessions")
    op.drop_column("telegram_sessions", "last_used_at")
