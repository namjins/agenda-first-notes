"""source_note_id on history tables (idempotent re-approval)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("follow_ups", "decisions", "calling_events", "scheduled_events")


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if "source_note_id" not in cols:  # 0001 created tables from current metadata on fresh installs
            op.add_column(table, sa.Column("source_note_id", sa.String(32), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("source_note_id")
