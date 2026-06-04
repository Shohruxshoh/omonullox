"""task_id full uuid

Revision ID: d4e5f6a7b8c9
Revises: c7d8e9f0a1b2
Create Date: 2026-04-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('task_logs', schema=None) as batch_op:
        batch_op.alter_column('task_id', type_=sa.String(length=36), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('task_logs', schema=None) as batch_op:
        batch_op.alter_column('task_id', type_=sa.String(length=8), existing_nullable=False)
