import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import bencodepy
import httpx
import pytest

from mae.db import Database
from mae.domain import DownloadHandle, Evidence, ReleaseDetails, SearchResult, Work
from mae.worker import Worker


@pytest.fixture
def series_env(settings, tmp_path, respx_mock):
    root = tmp_path / "store"
    folder = root / "downloads/serials"
    folder.mkdir(parents=True)
    for ep, language in [(1, "rus"), (2, "eng")]:
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
                "anullsrc",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
                "-metadata:s:a:0",
                f"language={language}",
                str(folder / f"Example.S01E{ep:02d}.1080p.WEB-DL.H264.mkv"),
            ],
            check=True,
            timeout=30,
        )
    settings = settings.model_copy(update={"store": root, "dry_run": False, "jellyfin_key": settings.api_key})
    respx_mock.post(settings.jellyfin_url + "/Library/Refresh").mock(return_value=httpx.Response(204))
    respx_mock.get(settings.jellyfin_url + "/Items").mock(
        return_value=httpx.Response(200, json={"Items": [{"Id": "show", "ProviderIds": {"Tvdb": "8"}}]})
    )
    episodes_route = respx_mock.get(settings.jellyfin_url + "/Shows/show/Episodes")
    episodes_route.mock(return_value=httpx.Response(200, json={"Items": []}))
    downloader = Mock()
    downloader.info.return_value = {"progress": 1, "state": "uploading", "save_path": str(folder)}
    work = Work(kind="series", external_id=8, title="Example", seasons=[1], episodes=["S01E01", "S01E02"])
    return settings, downloader, work, folder, episodes_route


def test_partial_import_and_disappeared_episode_never_complete_season(db, series_env):
    settings, downloader, work, folder, route = series_env
    job_id = db.submit(work, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    downloader.files.return_value = [{"name": "Example.S01E01.1080p.WEB-DL.H264.mkv"}]
    route.mock(
        return_value=httpx.Response(
            200,
            json={
                "Items": [
                    {"Path": "/data/store/serials/episode.mkv", "ParentIndexNumber": 1, "IndexNumber": 1}
                ]
            },
        )
    )
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)["state"] == "WAITING_FOR_RELEASE"
    assert db.job(job_id)["reason"] == "PARTIAL_SEASON"
    assert db.job(job_id)["torrent_hash"] is None
    imported = db.execute("SELECT * FROM imports")[0]
    assert Path(imported["destination"]).stat().st_ino == Path(imported["source"]).stat().st_ino
    assert db.execute("SELECT COUNT(*) n FROM episodes WHERE available=1")[0]["n"] == 1
    route.mock(return_value=httpx.Response(200, json={"Items": []}))
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)["reason"] == "IMPORTED_EPISODES_MISSING_FROM_LIBRARY"
    assert db.job(job_id)["state"] == "NEEDS_REVIEW"
    assert db.execute("SELECT COUNT(*) n FROM episodes WHERE available=1")[0]["n"] == 0
    downloader.add.assert_not_called()


def test_unmonitored_separate_pack_file_is_not_imported(db, series_env):
    settings, downloader, work, folder, route = series_env
    job_id = db.submit(work, False)
    ignored = db.execute("SELECT id FROM episodes WHERE job_id=:id AND episode='S01E02'", id=job_id)[0]["id"]
    db.set_episode_monitoring([ignored], False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    downloader.files.return_value = [{"name": path.name} for path in sorted(folder.glob("*.mkv"))]
    route.mock(
        return_value=httpx.Response(
            200,
            json={"Items": [{"Path": "/data/store/serials/e1.mkv", "ParentIndexNumber": 1, "IndexNumber": 1}]},
        )
    )

    Worker(settings, db, [], downloader).tick()

    imports = db.execute("SELECT episode FROM imports WHERE job_id=:id", id=job_id)
    assert [row["episode"] for row in imports] == ["S01E01"]
    assert db.job(job_id)["state"] == "AVAILABLE"


def test_unselected_pack_file_is_not_probed_or_imported(db, series_env):
    settings, downloader, work, folder, route = series_env
    job_id = db.submit(work, False)
    db.transition(
        job_id, "DOWNLOADING", torrent_hash="a" * 40,
        candidate=json.dumps({"parsed": {
            "download_episodes": ["S01E01"], "screen_size": "1080p", "video_codec": "H.264"
        }}),
    )
    downloader.files.return_value = [
        {"name": "Example.S01E01.1080p.WEB-DL.H264.mkv", "priority": 1},
        {"name": "Example.S01E02.1080p.WEB-DL.H264.mkv", "priority": 0},
    ]
    route.respond(200, json={"Items": [
        {"Path": "/data/store/serials/e1.mkv", "ParentIndexNumber": 1, "IndexNumber": 1}
    ]})

    Worker(settings, db, [], downloader).tick()

    imports = db.execute("SELECT episode FROM imports WHERE job_id=:id", id=job_id)
    assert [row["episode"] for row in imports] == ["S01E01"], db.job(job_id)["reason"]
    assert db.job(job_id)["state"] == "WAITING_FOR_RELEASE"
    assert (folder / "Example.S01E02.1080p.WEB-DL.H264.mkv").stat().st_nlink == 1


def test_combined_file_covering_unmonitored_episode_requires_review(db, series_env):
    settings, downloader, work, _folder, _route = series_env
    job_id = db.submit(work, False)
    ignored = db.execute("SELECT id FROM episodes WHERE job_id=:id AND episode='S01E02'", id=job_id)[0]["id"]
    db.set_episode_monitoring([ignored], False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    downloader.files.return_value = [{"name": "Example.S01E01-E02.1080p.WEB-DL.H264.mkv"}]

    Worker(settings, db, [], downloader).tick()

    assert db.job(job_id)["state"] == "NEEDS_REVIEW"
    assert db.job(job_id)["reason"] == "FILE_COVERS_UNMONITORED_EPISODE"
    assert not db.execute("SELECT * FROM imports")


def test_every_episode_probed_before_publishing_pack(db, series_env):
    settings, downloader, work, folder, _ = series_env
    job_id = db.submit(work, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    downloader.files.return_value = [{"name": p.name} for p in sorted(folder.glob("*.mkv"))]
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)["reason"] == "RUSSIAN_AUDIO_NOT_TAGGED"
    assert db.job(job_id)["state"] == "NEEDS_REVIEW"
    assert not db.execute("SELECT * FROM imports")
    assert not list((settings.store / "serials").glob("**/*.mkv"))


def test_partial_season_resume_then_new_episode_preserves_existing_files(db, series_env):
    settings, downloader, work, folder, route = series_env
    job_id = db.submit(work, False)
    first = folder / 'Example.S01E01.1080p.WEB-DL.H264.mkv'
    downloader.files.return_value = [{'name': first.name}]
    db.transition(job_id, 'DOWNLOADING', torrent_hash='a' * 40)
    existing = [{'Path': '/data/store/serials/e1.mkv', 'ParentIndexNumber': 1, 'IndexNumber': 1}]
    route.respond(200, json={'Items': existing})
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)['reason'] == 'PARTIAL_SEASON'
    original = db.execute('SELECT * FROM imports')[0]
    inode = Path(original['destination']).stat().st_ino

    next_folder = folder / 'next-release'
    next_folder.mkdir()
    second = next_folder / 'Example.S01E02.1080p.WEB-DL.H264.mkv'
    shutil.copyfile(first, second)  # Synthetic fixture, not engine import fallback.
    content = second.read_bytes()
    raw = bencodepy.encode({b'info': {
        b'name': second.name.encode(), b'length': len(content), b'piece length': 16384,
        b'pieces': b''.join(hashlib.sha1(content[i:i+16384]).digest() for i in range(0, len(content), 16384)),
    }})
    provider = Mock()
    provider.config.name = 'fixture'
    provider.search.return_value = [SearchResult(
        provider='fixture', release_id='episode-two', title=second.name, size=len(content), seeders=1,
    )]
    provider.fetch_details.return_value = ReleaseDetails(evidence=[
        Evidence(id='audio', text='Audio: Russian', method='fixture'),
    ])
    provider.resolve_download.return_value = DownloadHandle(url='http://fixture.test/release')
    provider.http.get.return_value = raw
    restarted = Database(settings.state_dir)
    restarted.execute('UPDATE jobs SET next_run=0 WHERE id=:id', id=job_id)
    Worker(settings, restarted, [provider], downloader).tick()
    assert provider.search.call_args.args[0].episodes == ['S01E02']
    assert restarted.job(job_id)['state'] == 'DOWNLOADING'
    downloader.add.assert_called_once()

    downloader.info.return_value = {'progress': 1, 'state': 'uploading', 'save_path': str(next_folder)}
    downloader.files.return_value = [{'name': second.name}]
    route.respond(200, json={'Items': existing + [
        {'Path': '/data/store/serials/e2.mkv', 'ParentIndexNumber': 1, 'IndexNumber': 2},
    ]})
    Worker(settings, restarted, [provider], downloader).tick()
    assert restarted.job(job_id)['state'] == 'AVAILABLE'
    assert len(restarted.execute('SELECT * FROM imports')) == 2
    assert Path(original['destination']).stat().st_ino == inode
    assert first.stat().st_nlink == second.stat().st_nlink == 2

    assert restarted.submit(work.model_copy(update={
        'episodes': ['S01E01', 'S01E02', 'S01E03'],
    }), False) == job_id
    assert restarted.job(job_id)['state'] == 'REQUESTED'
    provider.search.return_value = []
    Worker(settings, restarted, [provider], downloader).tick()
    assert provider.search.call_args.args[0].episodes == ['S01E03']
    assert restarted.job(job_id)['state'] == 'WAITING_FOR_RELEASE'
    downloader.add.assert_called_once()
    assert len(restarted.execute('SELECT * FROM imports')) == 2
