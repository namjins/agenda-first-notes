"""drop the per-workspace members table: the member list is now shared by both workspaces in <data>/members.sqlite3

The rows are dropped, not copied (no released data; the user reloads the CSV once). Name confirmations keep their
names, and rejections fall back to matching by name when their member id is gone. secure_delete zeroes the freed pages;
init_storage checkpoints the WAL after migrating.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS members")


def downgrade() -> None:
    pass  # the list stays in the shared store; older revisions run without a members table in the workspace
