"""add_telegram_sessions_and_dedup

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-02 13:45:00.000000

Yangi jadvallar:
  - telegram_sessions  : Telegram akkaunt sessiyalar
  - post_session_logs  : Kunlik deduplikatsiya

Mavjud jadvalga qo'shilgan ustunlar:
  - telegram_sessions.flood_until       (muddatli flood blok)
  - telegram_sessions.flood_count_today (bugungi flood soni)
  - telegram_sessions.flood_date        (flood sanasi)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. telegram_sessions jadvali ────────────────────────────────────────
    op.create_table(
        'telegram_sessions',
        sa.Column('id',               sa.Integer(),     nullable=False),
        sa.Column('uid',              sa.String(64),    nullable=False),
        sa.Column('api_id',           sa.String(20),    nullable=False),
        sa.Column('api_hash',         sa.String(64),    nullable=False),
        sa.Column('hash_session',     sa.Text(),         nullable=False),
        sa.Column('phone_model',      sa.String(128),   nullable=True),
        sa.Column('is_premium',       sa.String(4),     nullable=False, server_default='0'),
        sa.Column('username',         sa.String(64),    nullable=True),
        sa.Column('user_id',          sa.String(32),    nullable=True),
        sa.Column('phone_num',        sa.String(32),    nullable=True),
        sa.Column('app_version',      sa.String(32),    nullable=True),
        sa.Column('is_scam',          sa.String(8),     nullable=False, server_default='no'),
        sa.Column('app_name',         sa.String(64),    nullable=True),
        sa.Column('lang_code',        sa.String(16),    nullable=True),
        sa.Column('is_working',       sa.String(32),    nullable=False, server_default='sleep'),
        sa.Column('flood_until',      sa.String(50),    nullable=True),
        sa.Column('flood_count_today',sa.Integer(),     nullable=False, server_default='0'),
        sa.Column('flood_date',       sa.String(10),    nullable=True),
        sa.Column('pid_id',           sa.String(32),    nullable=True),
        sa.Column('date_last_online', sa.String(50),    nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uid', name='uq_telegram_sessions_uid'),
    )
    op.create_index('ix_telegram_sessions_uid',       'telegram_sessions', ['uid'],       unique=True)
    op.create_index('ix_telegram_sessions_user_id',   'telegram_sessions', ['user_id'],   unique=False)
    op.create_index('ix_telegram_sessions_phone_num', 'telegram_sessions', ['phone_num'], unique=False)

    # ── 2. post_session_logs jadvali (deduplikatsiya) ────────────────────────
    op.create_table(
        'post_session_logs',
        sa.Column('id',          sa.Integer(),   nullable=False),
        sa.Column('session_uid', sa.String(64),  nullable=False),
        sa.Column('post_link',   sa.Text(),       nullable=False),
        sa.Column('service',     sa.String(20),  nullable=False),
        sa.Column('done_date',   sa.String(10),  nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'session_uid', 'post_link', 'service', 'done_date',
            name='uq_session_post_day'
        ),
    )
    op.create_index('ix_post_session_logs_session_uid', 'post_session_logs', ['session_uid'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_post_session_logs_session_uid', table_name='post_session_logs')
    op.drop_table('post_session_logs')

    op.drop_index('ix_telegram_sessions_phone_num', table_name='telegram_sessions')
    op.drop_index('ix_telegram_sessions_user_id',   table_name='telegram_sessions')
    op.drop_index('ix_telegram_sessions_uid',       table_name='telegram_sessions')
    op.drop_table('telegram_sessions')
