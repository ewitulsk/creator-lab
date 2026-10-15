"""job dependencies

Revision ID: a789ff70b8b4
Revises: e3bcabc532b6
Create Date: 2026-10-14 23:43:03.217460

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a789ff70b8b4'
down_revision = 'e3bcabc532b6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('depends_on', sa.JSON(), nullable=False, server_default='[]'))

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.alter_column('depends_on', server_default=None)



def downgrade():
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('depends_on')

