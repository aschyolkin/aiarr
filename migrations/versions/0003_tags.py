"""Persistent Servarr-compatible labels and job associations."""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.execute("CREATE TABLE tags(id INTEGER PRIMARY KEY,label TEXT NOT NULL UNIQUE)")
    op.execute("""CREATE TABLE job_tags(
        job_id INTEGER NOT NULL REFERENCES jobs(id), tag_id INTEGER NOT NULL REFERENCES tags(id),
        PRIMARY KEY(job_id,tag_id))""")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
