import json
import time
from unittest.mock import Mock

import httpx

from mae.config import ProviderConfig
from mae.db import Database
from mae.domain import Outcome, ProviderError, ReleaseDetails, SearchResult
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


def test_empty_provider_does_not_misclassify_other_search_failure(settings, db, movie):
    empty, broken = Mock(), Mock()
    empty.config.name = "empty"
    empty.search.return_value = []
    broken.config.name = "broken"
    broken.search.side_effect = ProviderError(Outcome.TEMPORARY_FAILURE)
    job_id = db.submit(movie)
    downloader = Mock()
    Worker(settings, db, [empty, broken], downloader).tick()
    job = db.job(job_id)
    assert job["reason"] == "PROVIDER_SEARCH_FAILED"
    error = json.loads(job["decision"])["errors"][0]
    assert error["provider"] == "broken" and error["stage"] == "SEARCHING"
    downloader.add.assert_not_called()


def test_card_failure_preserves_stage_and_outcome(settings, db, movie):
    provider = Mock()
    provider.config.name = "fixture"
    provider.search.return_value = [SearchResult(
        provider="fixture", release_id="one", title="Example.2024.1080p.WEB-DL.H.264"
    )]
    provider.fetch_details.side_effect = ProviderError(Outcome.PARSER_BROKEN)
    job_id = db.submit(movie)
    downloader = Mock()
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert job["reason"] == "PROVIDER_ENRICHMENT_FAILED"
    error = json.loads(job["decision"])["errors"][0]
    assert error["stage"] == "ENRICHING" and error["outcome"] == "PARSER_BROKEN"
    downloader.add.assert_not_called()


def test_wrong_identity_unknown_fields_do_not_require_manual_review(settings, db, movie):
    provider = Mock()
    provider.config.name = 'fixture'
    provider.search.return_value = [SearchResult(
        provider='fixture',release_id='wrong',title='Unrelated.2024.1080p.WEB-DL.H264'
    )]
    job_id = db.submit(movie)
    downloader = Mock()
    Worker(settings,db,[provider],downloader).tick()
    assert db.job(job_id)['state'] == 'WAITING_FOR_RELEASE'
    provider.fetch_details.assert_not_called()
    downloader.add.assert_not_called()


def test_matching_unknown_audio_still_requires_review(settings, db, movie):
    provider = Mock()
    provider.config.name = 'fixture'
    provider.search.return_value = [SearchResult(
        provider='fixture',release_id='match',title='Example.2024.1080p.WEB-DL.H264',size=1000
    )]
    provider.fetch_details.return_value = ReleaseDetails()
    job_id = db.submit(movie)
    downloader = Mock()
    Worker(settings,db,[provider],downloader).tick()
    assert db.job(job_id)['state'] == 'NEEDS_REVIEW'
    downloader.add.assert_not_called()
