"""Release-card cache, releases rejected after download, journal clean-up and indexes."""
from alembic import op

revision = "0012"
down_revision = "0011"


def upgrade():
    # Release cards hold no credentials: the same text is already kept as evidence.
    op.execute("CREATE TABLE card_cache(provider TEXT NOT NULL, release_id TEXT NOT NULL, "
               "details TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(provider,release_id))")
    op.execute("CREATE TABLE rejected_releases(job_id INTEGER NOT NULL REFERENCES jobs(id), "
               "provider TEXT NOT NULL, release_id TEXT NOT NULL, torrent_hash TEXT, reason TEXT NOT NULL, "
               "created REAL NOT NULL, PRIMARY KEY(job_id,provider,release_id))")
    # Every 30-second download check used to journal an identical event.
    op.execute(
        "DELETE FROM events WHERE id IN (SELECT id FROM (SELECT id,state,reason,"
        "LAG(state) OVER (PARTITION BY job_id ORDER BY id) previous_state,"
        "LAG(reason) OVER (PARTITION BY job_id ORDER BY id) previous_reason,"
        "LAG(id) OVER (PARTITION BY job_id ORDER BY id) previous_id FROM events) "
        "WHERE previous_id IS NOT NULL AND state IS previous_state AND reason IS previous_reason)"
    )
    op.execute("CREATE INDEX events_job ON events(job_id,id)")
    op.execute("CREATE INDEX imports_job ON imports(job_id,state)")
    op.execute("CREATE INDEX jobs_due ON jobs(state,next_run)")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
