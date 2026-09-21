"""Persist per-request budgets and sanitized API outcomes."""
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade():
    op.execute("ALTER TABLE llm_usage ADD COLUMN request_key TEXT")
    op.execute("ALTER TABLE llm_usage ADD COLUMN charged_tokens INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE llm_usage ADD COLUMN latency_ms INTEGER")
    op.execute("ALTER TABLE llm_usage ADD COLUMN outcome TEXT NOT NULL DEFAULT 'LEGACY'")
    op.execute("UPDATE llm_usage SET charged_tokens=tokens")
    op.execute("CREATE INDEX llm_usage_request ON llm_usage(request_key)")


def downgrade():
    raise RuntimeError("Restore a stopped-service SQLite backup; destructive downgrade is disabled")
