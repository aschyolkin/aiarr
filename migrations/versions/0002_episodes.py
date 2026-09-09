"""Stable episode identifiers and observed library availability."""

import json

from alembic import op
from sqlalchemy import text

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute("""CREATE TABLE episodes (
        id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
        episode TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 0,
        UNIQUE(job_id,episode))""")
    conn = op.get_bind()
    for row in conn.execute(text("SELECT id,work FROM jobs WHERE identity LIKE 'series:%'")):
        for episode in sorted(set(json.loads(row.work)["episodes"])):
            conn.execute(
                text("INSERT INTO episodes(job_id,episode) VALUES(:job,:episode)"),
                {"job": row.id, "episode": episode},
            )


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
