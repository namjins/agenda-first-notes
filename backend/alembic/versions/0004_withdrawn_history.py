"""notes.source_node_id; status on calling_events / scheduled_events (unverified when gone from approved notes)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

COLUMNS = (("notes", "source_node_id", 32), ("calling_events", "status", 16), ("scheduled_events", "status", 16))


def upgrade() -> None:
    bind = op.get_bind()
    for table, column, length in COLUMNS:
        if column not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:  # fresh installs already have it
            op.add_column(table, sa.Column(column, sa.String(length), nullable=True))


def downgrade() -> None:
    for table, column, _ in COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
