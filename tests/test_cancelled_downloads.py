from unittest.mock import Mock

from mae.domain import Work
from mae.worker import Worker


def test_cancelled_owned_download_is_paused_once(settings, db, movie):
    job_id = db.submit(movie, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    db.cancel_from_admin(job_id)
    downloader = Mock()
    Worker(settings, db, [], downloader).tick()
    downloader.pause.assert_called_once_with("a" * 40)
    assert db.job(job_id)["cancel_pause_done"] == 1
    Worker(settings, db, [], downloader).tick()
    downloader.pause.assert_called_once()


def test_pause_failure_remains_pending_for_retry(settings, db, movie):
    job_id = db.submit(movie, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    db.cancel_from_admin(job_id)
    downloader = Mock()
    downloader.pause.side_effect = RuntimeError("offline")
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)["cancel_pause_done"] == 0


def test_unmonitor_last_episode_pauses_inflight_download(settings, db):
    series = Work(kind="series", external_id=8, title="Series", seasons=[1], episodes=["S01E01"])
    job_id = db.submit(series, False)
    episode_id = db.execute("SELECT id FROM episodes WHERE job_id=:id", id=job_id)[0]["id"]
    db.transition(job_id, "DOWNLOADING", torrent_hash="b" * 40)
    db.set_episode_monitoring([episode_id], False)
    assert (db.job(job_id)["state"], db.job(job_id)["reason"]) == (
        "NEEDS_REVIEW", "NO_MONITORED_EPISODES"
    )
    downloader = Mock()
    Worker(settings, db, [], downloader).tick()
    downloader.pause.assert_called_once_with("b" * 40)
    downloader.info.assert_not_called()
    Worker(settings, db, [], downloader).tick()
    downloader.pause.assert_called_once()
    assert db.job(job_id)["torrent_hash"] == "b" * 40


def test_unmonitor_last_episode_without_download_can_be_remonitored(settings, db):
    series = Work(kind="series", external_id=8, title="Series", seasons=[1], episodes=["S01E01"])
    job_id = db.submit(series, False)
    episode_id = db.execute("SELECT id FROM episodes WHERE job_id=:id", id=job_id)[0]["id"]
    db.set_episode_monitoring([episode_id], False)
    assert db.job(job_id)["reason"] == "NO_MONITORED_EPISODES"
    db.set_episode_monitoring([episode_id], True)
    assert db.job(job_id)["state"] == "REQUESTED"


def test_unmonitor_one_episode_pauses_pack_with_other_monitored_episodes(settings, db):
    series = Work(
        kind="series", external_id=8, title="Series", seasons=[1],
        episodes=["S01E01", "S01E02"],
    )
    job_id = db.submit(series, False)
    episode_id = db.execute(
        "SELECT id FROM episodes WHERE job_id=:id AND episode='S01E01'", id=job_id
    )[0]["id"]
    db.transition(job_id, "DOWNLOADING", torrent_hash="c" * 40)
    db.set_episode_monitoring([episode_id], False)
    assert db.desired_episodes(job_id) == ["S01E02"]
    assert db.job(job_id)["reason"] == "EPISODE_MONITORING_CHANGED"
    downloader = Mock()
    Worker(settings, db, [], downloader).tick()
    downloader.pause.assert_called_once_with("c" * 40)
    downloader.info.assert_not_called()


def test_unmonitor_available_episode_does_not_claim_library_item_missing(settings, db):
    series = Work(kind="series", external_id=8, title="Series", seasons=[1], episodes=["S01E01"])
    job_id = db.submit(series, False)
    episode_id = db.execute("SELECT id FROM episodes WHERE job_id=:id", id=job_id)[0]["id"]
    db.transition(job_id, "AVAILABLE")
    db.set_episode_monitoring([episode_id], False)
    assert db.observe_completed(db.job(job_id), set())
    assert db.job(job_id)["state"] == "AVAILABLE"
