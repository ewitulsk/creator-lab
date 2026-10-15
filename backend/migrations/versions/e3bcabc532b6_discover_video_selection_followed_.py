"""discover: video selection, followed channels

Channels: `tracking` becomes `followed`, `download_media` is replaced by
`auto_track_new` (off by default). Videos get a selection state (discovered or
tracked) and the outlier baseline.

Revision ID: e3bcabc532b6
Revises: 4fe394e30c20
Create Date: 2026-10-14 23:24:07.954816

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3bcabc532b6'
down_revision = '4fe394e30c20'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.alter_column('tracking', new_column_name='followed', existing_type=sa.Boolean(),
                              existing_nullable=False)
        batch_op.add_column(sa.Column('auto_track_new', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.drop_column('download_media')

    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('selection', sa.String(length=16), nullable=False,
                                      server_default='discovered'))
        batch_op.add_column(sa.Column('tracked_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('outlier_baseline', sa.Double(), nullable=True))
        batch_op.create_index('ix_videos_selection', ['selection'], unique=False)

    # Drop the server defaults again so the schema matches the models exactly.
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.alter_column('auto_track_new', server_default=None)
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.alter_column('selection', server_default=None)


def downgrade():
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.drop_index('ix_videos_selection')
        batch_op.drop_column('outlier_baseline')
        batch_op.drop_column('tracked_at')
        batch_op.drop_column('selection')

    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.add_column(sa.Column('download_media', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.drop_column('auto_track_new')
        batch_op.alter_column('followed', new_column_name='tracking', existing_type=sa.Boolean(),
                              existing_nullable=False)
