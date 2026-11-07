"""Source health journal, manual release choice, quality upgrades of library items."""
from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade():
    # One row per search or release-card request; pruned after 30 days.
    op.execute("CREATE TABLE provider_stats(id INTEGER PRIMARY KEY, provider TEXT NOT NULL, "
               "stage TEXT NOT NULL, outcome TEXT NOT NULL, latency_ms INTEGER NOT NULL, "
               "results INTEGER, created REAL NOT NULL)")
    op.execute("CREATE INDEX provider_stats_recent ON provider_stats(provider,created)")
    # {"provider": ..., "release_id": ...} picked by an administrator.
    op.execute("ALTER TABLE jobs ADD COLUMN manual_release TEXT")
    # 1 while a library item is being replaced by a better release.
    op.execute("ALTER TABLE jobs ADD COLUMN replacing INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE jobs ADD COLUMN upgrade_candidate TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN upgrade_checked REAL NOT NULL DEFAULT 0")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
