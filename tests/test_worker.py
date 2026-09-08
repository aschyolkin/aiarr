import time
from unittest.mock import Mock

import httpx

from mae.config import ProviderConfig
from mae.db import Database
from mae.domain import Outcome, ProviderError
from mae.providers import TorznabProvider
from mae.worker import Worker


def test_dry_run_restart_dedup(settings, db, movie, respx_mock):
    config = ProviderConfig(name="fixture", url="http://gateway.test/api", trusted_gateway=True, interval=0)
    p = TorznabProvider(config)
    xml = """<rss><channel><item><title>Example.2024.1080p.WEB-DL.H.264</title><guid>1</guid>
    <description>Audio: Russian</description><enclosure url="http://gateway.test/file" length="1000"/>
    </item></channel></rss>"""
    respx_mock.get(config.url).mock(return_value=httpx.Response(200, text=xml))
    job_id = db.submit(movie)
    downloader = Mock()
    Worker(settings, db, [p], downloader).tick()
    assert db.job(job_id)["reason"] == "DRY_RUN_SELECTED"
    assert not downloader.add.called
    restarted = Database(settings.state_dir)
    assert restarted.submit(movie) == job_id
    Worker(settings, restarted, [p], downloader).tick()
    assert len(restarted.jobs()) == 1 and not downloader.add.called


def test_missing_torrent_does_not_repeat_add(settings, db, movie):
    job_id = db.submit(movie, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    downloader = Mock()
    downloader.info.return_value = None
    Worker(settings, db, [], downloader).tick()
    assert not downloader.add.called
    assert db.job(job_id)["reason"] == "ADD_OUTCOME_UNCONFIRMED"


def test_source_errors_have_bounded_retries_and_retry_after(settings, db, movie):
    settings = settings.model_copy(update={"max_attempts": 2})
    provider = Mock()
    provider.config.name = "fixture"
    provider.search.side_effect = ProviderError(Outcome.RATE_LIMITED, retry_after=7200)
    downloader = Mock()
    job_id = db.submit(movie)
    before = time.time()
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert job["state"] == "RETRY_SCHEDULED" and job["attempts"] == 1
    assert job["next_run"] >= before + 7200
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    Worker(settings, db, [provider], downloader).tick()
    assert db.job(job_id)["state"] == "FAILED"
    assert db.job(job_id)["attempts"] == 2
    downloader.add.assert_not_called()
