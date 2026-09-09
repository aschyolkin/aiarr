from unittest.mock import Mock

import httpx

from mae.domain import Work
from mae.worker import Worker


def test_completed_library_disappearance_requires_review_not_redownload(settings, db, movie, respx_mock):
    settings = settings.model_copy(update={"jellyfin_key": settings.api_key})
    job_id = db.submit(movie)
    db.transition(job_id, "AVAILABLE", torrent_hash="a" * 40)
    route = respx_mock.get(settings.jellyfin_url + "/Items")
    route.mock(return_value=httpx.Response(503))
    downloader = Mock()
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)["state"] == "AVAILABLE"
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    route.mock(return_value=httpx.Response(200, json={"Items": []}))
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)["state"] == "NEEDS_REVIEW"
    assert db.job(job_id)["reason"] == "LIBRARY_ITEM_MISSING"
    downloader.add.assert_not_called()
    downloader.info.assert_not_called()


def test_stale_library_observation_cannot_override_new_episodes(db):
    work = Work(kind="series", external_id=7, title="Test", seasons=[1], episodes=["S01E01"])
    job_id = db.submit(work)
    db.transition(job_id, "AVAILABLE")
    snapshot = db.job(job_id)
    assert db.observe_completed(snapshot, {"S01E01"}) is True
    assert db.execute("SELECT available FROM episodes")[0]["available"] == 1
    db.submit(work.model_copy(update={"episodes": ["S01E01", "S01E02"]}))
    assert db.observe_completed(snapshot, set()) is False
    assert db.job(job_id)["state"] == "REQUESTED"
    assert db.execute("SELECT available FROM episodes ORDER BY episode")[0]["available"] == 1


def test_completed_series_ignores_unmonitored_episode(db):
    work = Work(kind="series", external_id=8, title="Example", episodes=["S01E01", "S01E02"])
    job_id = db.submit(work)
    ignored = db.execute("SELECT id FROM episodes WHERE job_id=:id AND episode='S01E02'", id=job_id)[0]["id"]
    db.set_episode_monitoring([ignored], False)
    db.transition(job_id, "AVAILABLE")

    assert db.observe_completed(db.job(job_id), {"S01E01"})
    assert db.job(job_id)["state"] == "AVAILABLE"
