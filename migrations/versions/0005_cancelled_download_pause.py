"""Record pause completion for torrents retained after task cancellation."""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade():
    op.execute("ALTER TABLE jobs ADD COLUMN cancel_pause_done INTEGER NOT NULL DEFAULT 0")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
