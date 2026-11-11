"""notes.name_confirmed (persisted provenance), name_confirmations.member_id, one confirmed name per heard text

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "name_confirmed" not in {c["name"] for c in insp.get_columns("notes")}:  # fresh installs already have it
        op.add_column("notes", sa.Column("name_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "member_id" not in {c["name"] for c in insp.get_columns("name_confirmations")}:
        op.add_column("name_confirmations", sa.Column("member_id", sa.String(32), nullable=True))
    # Recreated even when present: an early build indexed the heard text case-sensitively. Keep the earliest of any
    # duplicate confirmations (case variants included) made before this index existed.
    op.execute("DROP INDEX IF EXISTS uq_confirmed_heard")
    op.execute(
        "DELETE FROM name_confirmations WHERE status = 'confirmed' AND rowid NOT IN "
        "(SELECT MIN(rowid) FROM name_confirmations WHERE status = 'confirmed' GROUP BY meeting_id, lower(heard))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_confirmed_heard ON name_confirmations (meeting_id, lower(heard)) "
        "WHERE status = 'confirmed'"
    )


def downgrade() -> None:
    op.drop_index("uq_confirmed_heard", "name_confirmations")
    with op.batch_alter_table("name_confirmations") as batch:
        batch.drop_column("member_id")
    with op.batch_alter_table("notes") as batch:
        batch.drop_column("name_confirmed")
