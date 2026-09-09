"""Track Seerr request lifecycles without trusting missing pages as cancellation."""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade():
    op.execute("ALTER TABLE jobs ADD COLUMN generation INTEGER NOT NULL DEFAULT 1")
    op.execute("""CREATE TABLE seerr_requests(
        request_id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
        status INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 0,
        suppressed INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL,
        updated REAL NOT NULL
    )""")
    op.execute("CREATE INDEX ix_seerr_requests_job_active ON seerr_requests(job_id,active,suppressed)")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
