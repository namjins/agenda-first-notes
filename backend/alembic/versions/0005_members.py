"""members (per-workspace member list) and name_confirmations (per-meeting confirmed/rejected name suggestions)

Revision ID: 0005
Revises: 0004
"""

from agenda_first_notes.models import Base
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# The members table moved to the shared store (0007, maintainer decision 2026-11-08); this revision no longer creates it.
TABLES = ("name_confirmations",)


def upgrade() -> None:
    for t in TABLES:  # fresh installs already have them (0001 creates tables from current metadata)
        Base.metadata.tables[t].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
