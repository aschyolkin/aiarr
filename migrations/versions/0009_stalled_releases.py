"""Race a low-priority backup torrent; remember releases that never started."""
from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade():
    op.execute("CREATE TABLE stalled_releases(job_id INTEGER NOT NULL REFERENCES jobs(id), "
               "provider TEXT NOT NULL, release_id TEXT NOT NULL, torrent_hash TEXT, created REAL NOT NULL, "
               "PRIMARY KEY(job_id,provider,release_id))")
    op.execute("ALTER TABLE jobs ADD COLUMN backup_hash TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN backup_candidate TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN backup_reserved INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE jobs ADD COLUMN download_started REAL")
    op.execute("CREATE TABLE discard_downloads(job_id INTEGER NOT NULL REFERENCES jobs(id), "
               "torrent_hash TEXT NOT NULL, PRIMARY KEY(job_id,torrent_hash))")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
