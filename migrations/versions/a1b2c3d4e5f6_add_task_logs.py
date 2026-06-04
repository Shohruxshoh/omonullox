"""add_task_logs

Revision ID: a1b2c3d4e5f6
Revises: bcc3db564546
Create Date: 2026-04-01 20:49:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'bcc3db564546'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """task_logs jadvalini yaratadi."""
    op.create_table(
        'task_logs',
        sa.Column('task_id',     sa.String(length=8),  nullable=False),
        sa.Column('api_key',     sa.String(length=36), nullable=False),
        sa.Column('service',     sa.String(length=20), nullable=False),
        sa.Column('post_link',   sa.Text(),             nullable=False),
        sa.Column('priority',    sa.Integer(),          nullable=False),
        sa.Column('status',      sa.String(length=20), nullable=False),
        sa.Column('total',       sa.Integer(),          nullable=False),
        sa.Column('done',        sa.Integer(),          nullable=False),
        sa.Column('error',       sa.Text(),             nullable=True),
        sa.Column('created_at',  sa.String(length=50), nullable=False),
        sa.Column('started_at',  sa.String(length=50), nullable=True),
        sa.Column('finished_at', sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint('task_id'),
    )
    op.create_index('ix_task_logs_api_key', 'task_logs', ['api_key'], unique=False)


def downgrade() -> None:
    """task_logs jadvalini o'chiradi."""
    op.drop_index('ix_task_logs_api_key', table_name='task_logs')
    op.drop_table('task_logs')
