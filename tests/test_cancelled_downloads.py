from unittest.mock import Mock

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
