from alembic import command
from alembic.config import Config

from mae.db import Database
from mae.domain import Work


def test_existing_jobs_migrate_without_assuming_availability(tmp_path, monkeypatch):
    directory = tmp_path / "old-state"
    monkeypatch.setenv("MAE_API_KEY", "migration-test-key-000000000000")
    monkeypatch.setenv("MAE_STATE_DIR", str(directory))
    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    db = Database(directory)
    work = Work(kind="series", external_id=7, title="Old series", seasons=[2], episodes=["S02E02", "S02E01"])
    db.execute(
        "INSERT INTO jobs(identity,work,state) VALUES('series:7',:work,'AVAILABLE')",
        work=work.model_dump_json(),
    )
    command.upgrade(config, "head")
    rows = db.execute("SELECT * FROM episodes ORDER BY episode")
    assert [r["episode"] for r in rows] == ["S02E01", "S02E02"]
    assert not any(r["available"] for r in rows)
    assert db.jobs()[0]["state"] == "AVAILABLE"
    command.upgrade(config, "head")
    assert db.execute("SELECT * FROM episodes ORDER BY episode") == rows
