import subprocess
from unittest.mock import Mock

import httpx

from mae.db import Database
from mae.importing import destination_for
from mae.worker import Worker


def test_restart_between_link_and_journal_commit(settings, db, movie, tmp_path, monkeypatch, respx_mock):
    root = tmp_path / "store"
    directory = root / "downloads/films"
    directory.mkdir(parents=True)
    source = directory / "Example.2024.1080p.WEB-DL.H264.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=1920x1080:r=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-metadata:s:a:0",
            "language=rus",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    settings = settings.model_copy(update={"store": root, "dry_run": False, "jellyfin_key": settings.api_key})
    job_id = db.submit(movie, dry_run=False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    downloader = Mock()
    downloader.info.return_value = {"progress": 1, "save_path": str(directory), "state": "uploading"}
    downloader.files.return_value = [{"name": source.name}]
    original_execute = db.execute
    interrupted = False

    def fail_journal_once(sql, **params):
        nonlocal interrupted
        if "UPDATE imports SET state='PUBLISHED'" in sql and not interrupted:
            interrupted = True
            raise RuntimeError("SIMULATED_CRASH_AFTER_LINK")
        return original_execute(sql, **params)

    monkeypatch.setattr(db, "execute", fail_journal_once)
    Worker(settings, db, [], downloader).tick()
    target = destination_for(movie, source, root)
    assert target.exists()
    assert db.job(job_id)["state"] == "RETRY_SCHEDULED"
    assert original_execute("SELECT state FROM imports")[0]["state"] == "PREPARED"
    restarted = Database(settings.state_dir)
    restarted.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    respx_mock.post(settings.jellyfin_url + "/Library/Refresh").mock(return_value=httpx.Response(204))
    respx_mock.get(settings.jellyfin_url + "/Items").mock(
        return_value=httpx.Response(
            200, json={"Items": [{"ProviderIds": {"Tmdb": "1"}, "Path": str(target)}]}
        )
    )
    Worker(settings, restarted, [], downloader).tick()
    assert restarted.job(job_id)["state"] == "AVAILABLE"
    assert source.stat().st_ino == target.stat().st_ino
    assert source.stat().st_nlink == 2
    assert len(restarted.execute("SELECT * FROM imports")) == 1
    downloader.add.assert_not_called()
