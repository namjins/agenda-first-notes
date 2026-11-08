"""notes.source_text / notes.user_content (provenance of user-touched notes)

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("notes")}
    if "source_text" not in cols:  # 0001 created tables from current metadata on fresh installs
        op.add_column("notes", sa.Column("source_text", sa.Text(), nullable=True))
    if "user_content" not in cols:
        op.add_column("notes", sa.Column("user_content", sa.Boolean(), nullable=False, server_default=sa.false()))
        # Existing user notes: we cannot tell a move from a rewrite, so treat them as user claims (conservative).
        op.execute("UPDATE notes SET user_content = 1 WHERE origin = 'user'")


def downgrade() -> None:
    with op.batch_alter_table("notes") as batch:
        batch.drop_column("user_content")
        batch.drop_column("source_text")
