"""Separate user-desired episode coverage from Seerr-approved coverage."""
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade():
    op.execute("ALTER TABLE episodes ADD COLUMN monitored INTEGER NOT NULL DEFAULT 1")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
