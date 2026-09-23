import os
from unittest.mock import Mock

import pytest

from mae.downloads import Downloader
from mae.worker import Worker
from tests.test_seerr_reconciliation import MutableSeerr, request


class DeletingSeerr(MutableSeerr):
    def __init__(self, requests, missing=()):
        super().__init__(requests)
        self.missing = set(missing)

    def request_status(self, request_id):
        return None if request_id in self.missing else {"id": request_id, "status": 2}


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "store"
    for name in ("films", "serials", "downloads/films"):
        (root / name).mkdir(parents=True)
    return root


def published(db, settings, store, movie):
    DeletingSeerr([request(100, 2)]).reconcile(db, False)
    job_id = db.execute("SELECT job_id FROM seerr_requests WHERE request_id=100")[0]["job_id"]
    source = store / "downloads/films/Example.2024/movie.mkv"
    source.parent.mkdir()
    source.write_bytes(b"x")
    destination = store / "films/Example (2024) [tmdbid-1]/Example (2024) [tmdbid-1].mkv"
    destination.parent.mkdir()
    os.link(source, destination)
    info = destination.stat()
    db.execute(
        "INSERT INTO imports(job_id,source,destination,state,probe,device,inode) "
        "VALUES(:id,:src,:dst,'PUBLISHED','{}',:dev,:ino)",
        id=job_id, src=str(source), dst=str(destination), dev=info.st_dev, ino=info.st_ino,
    )
    db.transition(job_id, "AVAILABLE", torrent_hash="d" * 40)
    return job_id, destination


def worker(settings, store, db, downloader):
    current = settings.model_copy(update={"store": store, "dry_run": False})
    return Worker(current, db, [], downloader)


def test_list_omission_without_404_keeps_job(settings, db, movie, store):
    job_id, destination = published(db, settings, store, movie)
    DeletingSeerr([]).reconcile(db, False)
    downloader = Mock()
    worker(settings, store, db, downloader).tick()
    assert db.job(job_id)["state"] == "AVAILABLE"
    assert destination.exists()
    downloader.delete_owned.assert_not_called()


def test_confirmed_404_removes_owned_file_and_torrent_once(settings, db, movie, store):
    job_id, destination = published(db, settings, store, movie)
    DeletingSeerr([], missing={100}).reconcile(db, False)
    assert (db.job(job_id)["state"], db.job(job_id)["reason"]) == ("CANCELLED", "SEERR_REQUEST_DELETED")
    downloader = Mock()
    worker(settings, store, db, downloader).tick()
    assert not destination.exists() and not destination.parent.exists()
    assert (store / "films").is_dir()
    downloader.delete_owned.assert_called_once_with("d" * 40, store / "downloads")
    downloader.pause.assert_not_called()
    job = db.job(job_id)
    assert (job["cancel_pause_done"], job["torrent_hash"]) == (1, None)
    assert db.execute("SELECT COUNT(*) n FROM imports")[0]["n"] == 0
    worker(settings, store, db, downloader).tick()
    downloader.delete_owned.assert_called_once()


def test_inode_mismatch_keeps_every_file(settings, db, movie, store):
    job_id, destination = published(db, settings, store, movie)
    destination.unlink()
    destination.write_bytes(b"user replacement")
    DeletingSeerr([], missing={100}).reconcile(db, False)
    downloader = Mock()
    worker(settings, store, db, downloader).tick()
    assert destination.read_bytes() == b"user replacement"
    downloader.delete_owned.assert_not_called()
    assert db.job(job_id)["cancel_pause_done"] == 0
    assert db.execute("SELECT reason FROM events ORDER BY id DESC LIMIT 1")[0]["reason"] == (
        "IMPORT_INODE_MISMATCH"
    )


def test_torrent_failure_is_retried_without_repeating_file_removal(settings, db, movie, store):
    job_id, destination = published(db, settings, store, movie)
    DeletingSeerr([], missing={100}).reconcile(db, False)
    downloader = Mock()
    downloader.delete_owned.side_effect = RuntimeError("qbit offline")
    worker(settings, store, db, downloader).tick()
    assert not destination.exists()
    assert db.job(job_id)["cancel_pause_done"] == 0
    downloader.delete_owned.side_effect = None
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    worker(settings, store, db, downloader).tick()
    assert db.job(job_id)["cancel_pause_done"] == 1


def test_another_active_request_keeps_the_job(settings, db, movie, store):
    job_id, destination = published(db, settings, store, movie)
    DeletingSeerr([request(100, 2), request(101, 2)]).reconcile(db, False)
    DeletingSeerr([request(101, 2)], missing={100}).reconcile(db, False)
    assert db.job(job_id)["state"] == "AVAILABLE"
    assert destination.exists()


def test_dry_run_never_deletes(settings, db, movie, store):
    job_id, destination = published(db, settings, store, movie)
    DeletingSeerr([], missing={100}).reconcile(db, False)
    current = settings.model_copy(update={"store": store, "dry_run": True})
    downloader = Mock()
    Worker(current, db, [], downloader).tick()
    assert destination.exists()
    downloader.delete_owned.assert_not_called()


def owned_downloader(row, files=({"name": "Example/movie.mkv"},)):
    downloader = Downloader.__new__(Downloader)
    downloader.client = Mock()
    downloader.client.torrents_info.side_effect = [[row], []]
    downloader.client.torrents_files.return_value = list(files)
    return downloader


BASE = {"category": "mae", "tags": "media-acquisition-engine", "save_path": "/data/store/downloads/films"}


def test_delete_owned_requires_tag_and_managed_path():
    from pathlib import Path

    root = Path("/data/store/downloads")
    for row, code in [
        ({**BASE, "tags": ""}, "TORRENT_OWNERSHIP_TAG_MISSING"),
        ({**BASE, "save_path": "/data/store/films"}, "TORRENT_OUTSIDE_MANAGED_PATH"),
        ({**BASE, "save_path": "/data/store/downloads/../films"}, "TORRENT_OUTSIDE_MANAGED_PATH"),
    ]:
        downloader = owned_downloader(row)
        with pytest.raises(ValueError, match=code):
            downloader.delete_owned("e" * 40, root)
        downloader.client.torrents_delete.assert_not_called()
    with pytest.raises(ValueError, match="TORRENT_OWNED_BY_ANOTHER_SERVICE"):
        owned_downloader({**BASE, "category": "radarr"}).delete_owned("e" * 40, root)
    with pytest.raises(ValueError, match="UNSAFE_TORRENT_PATH"):
        owned_downloader(BASE, [{"name": "../escape.mkv"}]).delete_owned("e" * 40, root)
    downloader = owned_downloader(BASE)
    assert downloader.delete_owned("e" * 40, root)
    downloader.client.torrents_delete.assert_called_once_with(delete_files=True, torrent_hashes="e" * 40)
