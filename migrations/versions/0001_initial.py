"""Durable jobs, decisions and import journal."""

from alembic import op

revision = "0001"
down_revision = None

SCHEMA = [
    """CREATE TABLE jobs (
      id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE, work TEXT NOT NULL,
      state TEXT NOT NULL, reason TEXT, dry_run INTEGER NOT NULL DEFAULT 1,
      attempts INTEGER NOT NULL DEFAULT 0, next_run REAL NOT NULL DEFAULT 0,
      candidate TEXT, decision TEXT, torrent_hash TEXT, reserved INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE TABLE events(id INTEGER PRIMARY KEY,job_id INTEGER REFERENCES jobs(id),state TEXT,reason TEXT,created REAL)",
    """CREATE TABLE imports(id INTEGER PRIMARY KEY,job_id INTEGER REFERENCES jobs(id),
       source TEXT NOT NULL,destination TEXT NOT NULL UNIQUE,episode TEXT,
       state TEXT NOT NULL,probe TEXT NOT NULL,device INTEGER,inode INTEGER)""",
    "CREATE TABLE llm_cache(key TEXT PRIMARY KEY,response TEXT NOT NULL,created REAL NOT NULL)",
    "CREATE TABLE llm_usage(id INTEGER PRIMARY KEY,day TEXT NOT NULL,tokens INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE attempts(id INTEGER PRIMARY KEY,job_id INTEGER REFERENCES jobs(id),created REAL NOT NULL,state TEXT NOT NULL,reason TEXT)",
]


def upgrade():
    for statement in SCHEMA:
        op.execute(statement)


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
