"""Keep download ownership after a partial season clears the active hash."""
from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade():
    op.execute("CREATE TABLE job_downloads(job_id INTEGER NOT NULL REFERENCES jobs(id), "
               "torrent_hash TEXT NOT NULL, PRIMARY KEY(job_id,torrent_hash))")
    op.execute("INSERT INTO job_downloads SELECT id,torrent_hash FROM jobs WHERE torrent_hash IS NOT NULL")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
