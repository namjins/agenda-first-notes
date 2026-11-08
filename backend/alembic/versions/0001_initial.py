"""initial schema

Revision ID: 0001
Revises:
"""

from agenda_first_notes.models import Base
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5("
        "entity_type UNINDEXED, entity_id UNINDEXED, meeting_id UNINDEXED, "
        "meeting_date UNINDEXED, status UNINDEXED, title, body)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search_index")
    Base.metadata.drop_all(op.get_bind())
