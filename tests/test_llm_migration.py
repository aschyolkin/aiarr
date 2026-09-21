from alembic import command
from alembic.config import Config

from mae.db import Database


def test_usage_migration_preserves_billed_tokens_and_is_idempotent(tmp_path, monkeypatch):
    directory = tmp_path / 'old-state'
    monkeypatch.setenv('MAE_API_KEY', 'migration-test-key-000000000000')
    monkeypatch.setenv('MAE_STATE_DIR', str(directory))
    config = Config('alembic.ini')
    command.upgrade(config, '0006')
    db = Database(directory)
    db.execute("INSERT INTO llm_usage(day,tokens) VALUES('2026-09-20',441)")
    command.upgrade(config, 'head')
    row = db.execute('SELECT * FROM llm_usage')[0]
    assert row['tokens'] == row['charged_tokens'] == 441
    assert row['outcome'] == 'LEGACY'
    assert row['latency_ms'] is None and row['request_key'] is None
    command.upgrade(config, 'head')
    assert db.execute('SELECT * FROM llm_usage') == [row]
