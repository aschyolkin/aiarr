"""Distinguish slow releases, kept as a last resort, from dead ones."""
from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade():
    op.execute("ALTER TABLE stalled_releases ADD COLUMN slow INTEGER NOT NULL DEFAULT 0")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
