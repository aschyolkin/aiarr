from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from mae.config import Settings
from mae.db import Database
from mae.domain import Work


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MAE_API_KEY", "offline-test-key-0000000000000000")
    monkeypatch.setenv("MAE_STATE_DIR", str(tmp_path / "state"))
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    return Settings(_env_file=None)


@pytest.fixture
def db(settings):
    return Database(settings.state_dir)


@pytest.fixture
def movie():
    return Work(kind="movie", external_id=1, title="Example", year=2024)
