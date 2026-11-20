"""Migration 002: Add publishing state machine columns, idempotency key, and permalink tracking

Revision ID: 002_publishing_state_machine
Revises: 001_initial_schema
Create Date: 2026-11-20 16:40:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_publishing_state_machine'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == 'sqlite'

    # 1. video_assets: add crop_mode
    op.add_column(
        'video_assets',
        sa.Column('crop_mode', sa.String(length=50), nullable=True)
    )

    # 2. publishing_jobs: convert status enum to VARCHAR(50) for rich state machine
    if is_sqlite:
        with op.batch_alter_table('publishing_jobs') as batch_op:
            batch_op.alter_column('status', type_=sa.String(length=50))
    else:
        op.alter_column(
            'publishing_jobs',
            'status',
            type_=sa.String(length=50),
            existing_type=sa.Enum('PENDING', 'PROCESSING', 'FINISHED', 'FAILED', 'SIMULATED', name='jobstatus'),
            existing_nullable=False,
            postgresql_using='status::text'
        )

    # publishing_jobs: add state machine, diagnostics, and CDN metadata columns
    op.add_column('publishing_jobs', sa.Column('current_stage', sa.String(length=100), nullable=True, server_default='QUEUED'))
    op.add_column('publishing_jobs', sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('publishing_jobs', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.add_column('publishing_jobs', sa.Column('instagram_media_id', sa.String(length=255), nullable=True))
    op.add_column('publishing_jobs', sa.Column('cloudinary_public_id', sa.String(length=255), nullable=True))
    op.add_column('publishing_jobs', sa.Column('permalink', sa.Text(), nullable=True))
    op.add_column('publishing_jobs', sa.Column('diagnostic_metadata', sa.Text(), nullable=True))
    op.add_column('publishing_jobs', sa.Column('published_at', sa.DateTime(), nullable=True))

    # Unique constraint and index on idempotency_key
    if is_sqlite:
        op.create_index('ix_publishing_jobs_idempotency_key', 'publishing_jobs', ['idempotency_key'], unique=True)
    else:
        op.create_unique_constraint('uq_publishing_jobs_idempotency_key', 'publishing_jobs', ['idempotency_key'])
        op.create_index('ix_publishing_jobs_idempotency_key', 'publishing_jobs', ['idempotency_key'])

    # 3. publishing_history: convert status enum to VARCHAR(50)
    if is_sqlite:
        with op.batch_alter_table('publishing_history') as batch_op:
            batch_op.alter_column('status', type_=sa.String(length=50))
    else:
        op.alter_column(
            'publishing_history',
            'status',
            type_=sa.String(length=50),
            existing_type=sa.Enum('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'SIMULATED', name='publishstatus'),
            existing_nullable=False,
            postgresql_using='status::text'
        )

    # publishing_history: add live Instagram permalink
    op.add_column('publishing_history', sa.Column('permalink', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == 'sqlite'

    # 1. publishing_history
    op.drop_column('publishing_history', 'permalink')
    if is_sqlite:
        with op.batch_alter_table('publishing_history') as batch_op:
            batch_op.alter_column('status', type_=sa.Enum('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'SIMULATED', name='publishstatus'))
    else:
        op.alter_column(
            'publishing_history',
            'status',
            type_=sa.Enum('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'SIMULATED', name='publishstatus'),
            existing_type=sa.String(length=50),
            existing_nullable=False,
            postgresql_using='status::publishstatus'
        )

    # 2. publishing_jobs
    op.drop_index('ix_publishing_jobs_idempotency_key', table_name='publishing_jobs')
    if not is_sqlite:
        op.drop_constraint('uq_publishing_jobs_idempotency_key', 'publishing_jobs', type_='unique')
    op.drop_column('publishing_jobs', 'published_at')
    op.drop_column('publishing_jobs', 'diagnostic_metadata')
    op.drop_column('publishing_jobs', 'permalink')
    op.drop_column('publishing_jobs', 'cloudinary_public_id')
    op.drop_column('publishing_jobs', 'instagram_media_id')
    op.drop_column('publishing_jobs', 'idempotency_key')
    op.drop_column('publishing_jobs', 'attempt_count')
    op.drop_column('publishing_jobs', 'current_stage')

    if is_sqlite:
        with op.batch_alter_table('publishing_jobs') as batch_op:
            batch_op.alter_column('status', type_=sa.Enum('PENDING', 'PROCESSING', 'FINISHED', 'FAILED', 'SIMULATED', name='jobstatus'))
    else:
        op.alter_column(
            'publishing_jobs',
            'status',
            type_=sa.Enum('PENDING', 'PROCESSING', 'FINISHED', 'FAILED', 'SIMULATED', name='jobstatus'),
            existing_type=sa.String(length=50),
            existing_nullable=False,
            postgresql_using='status::jobstatus'
        )

    # 3. video_assets
    op.drop_column('video_assets', 'crop_mode')
