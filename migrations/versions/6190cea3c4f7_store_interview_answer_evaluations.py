"""store interview answer evaluations

Revision ID: 6190cea3c4f7
Revises: 7c3d4e5f6a7b
Create Date: 2026-10-19 00:02:44.014621
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '6190cea3c4f7'
down_revision: Union[str, None] = '7c3d4e5f6a7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('attempts', sa.Column('evaluation', sa.JSON(none_as_null=True), nullable=True))

def downgrade() -> None:
    op.drop_column('attempts', 'evaluation')
