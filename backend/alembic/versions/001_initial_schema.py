"""Initial migration: channels, videos, video_assets, instagram_accounts, publishing_jobs, publishing_history

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-11-19 11:35:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. channels
    op.create_table(
        'channels',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('youtube_channel_id', sa.String(length=255), nullable=False, unique=True),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('handle', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('thumbnail_url', sa.Text(), nullable=True),
        sa.Column('banner_url', sa.Text(), nullable=True),
        sa.Column('subscriber_count', sa.BigInteger(), nullable=True),
        sa.Column('video_count', sa.Integer(), nullable=True),
        sa.Column('channel_url', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_channels_youtube_id', 'channels', ['youtube_channel_id'])

    # 2. videos
    op.create_table(
        'videos',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('channel_id', sa.String(length=36), sa.ForeignKey('channels.id', ondelete='CASCADE'), nullable=True),
        sa.Column('youtube_video_id', sa.String(length=255), nullable=False, unique=True),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('thumbnail_url', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('view_count', sa.BigInteger(), nullable=True),
        sa.Column('video_url', sa.Text(), nullable=False),
        sa.Column('channel_name', sa.String(length=500), nullable=True),
        sa.Column('channel_url', sa.Text(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.Enum('IMPORTED', 'PROCESSING', 'PROCESSED', 'FAILED', 'READY', name='videostatus'), nullable=False, server_default='IMPORTED'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_videos_youtube_id', 'videos', ['youtube_video_id'])
    op.create_index('ix_videos_status', 'videos', ['status'])

    # 3. video_assets
    op.create_table(
        'video_assets',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('video_id', sa.String(length=36), sa.ForeignKey('videos.id', ondelete='CASCADE'), nullable=False),
        sa.Column('original_filename', sa.String(length=1000), nullable=True),
        sa.Column('file_path', sa.Text(), nullable=True),
        sa.Column('cloudinary_public_id', sa.String(length=500), nullable=True),
        sa.Column('cloudinary_url', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('codec', sa.String(length=100), nullable=True),
        sa.Column('file_size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('is_processed', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_reel_compatible', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('processing_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # 4. instagram_accounts
    op.create_table(
        'instagram_accounts',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('instagram_user_id', sa.String(length=255), nullable=False, unique=True),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=500), nullable=True),
        sa.Column('profile_picture_url', sa.Text(), nullable=True),
        sa.Column('account_type', sa.String(length=50), nullable=True),
        sa.Column('followers_count', sa.Integer(), nullable=True),
        sa.Column('following_count', sa.Integer(), nullable=True),
        sa.Column('encrypted_access_token', sa.Text(), nullable=True),
        sa.Column('token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.Enum('NOT_CONNECTED', 'CONNECTED', 'TOKEN_EXPIRED', 'ERROR', name='instagramaccountstatus'), nullable=False, server_default='NOT_CONNECTED'),
        sa.Column('connected_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_instagram_accounts_user_id', 'instagram_accounts', ['instagram_user_id'])

    # 5. publishing_jobs
    op.create_table(
        'publishing_jobs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('video_id', sa.String(length=36), sa.ForeignKey('videos.id'), nullable=False),
        sa.Column('instagram_account_id', sa.String(length=36), sa.ForeignKey('instagram_accounts.id'), nullable=True),
        sa.Column('video_asset_id', sa.String(length=36), sa.ForeignKey('video_assets.id'), nullable=True),
        sa.Column('caption', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'FINISHED', 'FAILED', 'SIMULATED', name='jobstatus'), nullable=False, server_default='PENDING'),
        sa.Column('instagram_container_id', sa.String(length=255), nullable=True),
        sa.Column('cloudinary_url', sa.Text(), nullable=True),
        sa.Column('processing_progress', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('rights_confirmed', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('scheduled_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_publishing_jobs_status', 'publishing_jobs', ['status'])
    op.create_index('ix_publishing_jobs_video_id', 'publishing_jobs', ['video_id'])

    # 6. publishing_history
    op.create_table(
        'publishing_history',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('publishing_job_id', sa.String(length=36), sa.ForeignKey('publishing_jobs.id'), nullable=False),
        sa.Column('video_id', sa.String(length=36), sa.ForeignKey('videos.id'), nullable=False),
        sa.Column('instagram_account_id', sa.String(length=36), sa.ForeignKey('instagram_accounts.id'), nullable=True),
        sa.Column('video_title', sa.String(length=1000), nullable=False),
        sa.Column('instagram_username', sa.String(length=255), nullable=True),
        sa.Column('instagram_media_id', sa.String(length=255), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'PUBLISHED', 'FAILED', 'SIMULATED', name='publishstatus'), nullable=False, server_default='PENDING'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_publishing_history_status', 'publishing_history', ['status'])


def downgrade() -> None:
    op.drop_table('publishing_history')
    op.drop_table('publishing_jobs')
    op.drop_table('instagram_accounts')
    op.drop_table('video_assets')
    op.drop_table('videos')
    op.drop_table('channels')
    op.execute('DROP TYPE IF EXISTS publishstatus')
    op.execute('DROP TYPE IF EXISTS jobstatus')
    op.execute('DROP TYPE IF EXISTS instagramaccountstatus')
    op.execute('DROP TYPE IF EXISTS videostatus')
