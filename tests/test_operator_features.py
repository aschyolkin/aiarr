"""Manual release choice, library upgrades, airing series, source health, speed schedule, cleanup."""
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mae.config import Policy
from mae.domain import Candidate, DownloadHandle, Evidence, ReleaseDetails, SearchResult, Work
from mae.parsing import parse_candidate
from mae.policies import evaluate
from mae.worker import Worker


def release(title, rid="r1", provider="fixture", size=4 * 1024**3, seeders=10):
    return SearchResult(provider=provider, release_id=rid, title=title, size=size, seeders=seeders)


def fake_provider(results, card="Аудио: русский", name="fixture"):
    provider = Mock()
    provider.config.name = name
    provider.search.return_value = results
    provider.fetch_details.return_value = ReleaseDetails(
        evidence=[Evidence(id="card", text=card, method="fixture")]
    )
    provider.resolve_download.return_value = DownloadHandle(url="magnet:?xt=urn:btih:" + "b" * 40)
    return provider


def magnet_downloader(size=4 * 1024**3, name="Example.2024.2160p.WEB-DL.H.265.mkv"):
    downloader = Mock()
    downloader.magnet_preflight.return_value = ("b" * 40, size, [(name, size)])
    return downloader


# ---------- voice-over and studio preferences


def test_title_voice_codes_and_studio_score(movie):
    policy = Policy(voice_weights={"Dub": 15, "MVO": 10}, studio_weights={"LostFilm": 7})
    dub = evaluate(parse_candidate(release("Example (2024) WEB-DL 1080p | D", "a"), ReleaseDetails()), movie, policy)
    mvo = evaluate(parse_candidate(release("Example (2024) WEB-DL 1080p MVO | LostFilm", "b"), ReleaseDetails()),
                   movie, policy)
    assert {c.value for c in dub.claims if c.field == "voice"} == {"Dub"}
    assert dub.score["voice"] == 15
    assert {c.value for c in mvo.claims if c.field == "studio"} == {"LostFilm"}
    assert (mvo.score["voice"], mvo.score["studio"]) == (10, 7)


def test_translation_line_names_voice_and_studio(movie):
    card = "Перевод: Профессиональный (дублированный) Пифагор"
    c = parse_candidate(release("Example.2024.1080p.WEB-DL.H.264"), ReleaseDetails(
        evidence=[Evidence(id="card", text=card, method="fixture")]))
    assert {x.value for x in c.claims if x.field == "voice"} == {"Dub"}
    assert {x.value for x in c.claims if x.field == "studio"} == {"Пифагор"}


def test_custom_policy_studio_is_recognized(movie):
    c = parse_candidate(release("Example (2024) WEB-DL 1080p | Дубляж Мосфильм Мастер"), ReleaseDetails(),
                        ["Мосфильм Мастер"])
    assert {x.value for x in c.claims if x.field == "studio"} == {"Мосфильм Мастер"}


def test_bare_abbreviation_in_title_is_not_a_studio(movie):
    c = parse_candidate(release("Example.2024.1080p.RHS.WEB-DL"), ReleaseDetails())
    assert not [x for x in c.claims if x.field == "studio"]


# ---------- airing series


def test_unaired_episodes_wait_until_air_date(settings, db, monkeypatch):
    settings = settings.model_copy(update={"jellyfin_key": settings.api_key, "dry_run": False})
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _s, _w: {"S01E01"})
    future = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 5 * 86400))
    work = Work(kind="series", external_id=8, title="Example", seasons=[1], episodes=["S01E01", "S01E02"],
                air_dates={"S01E01": "2020-01-01", "S01E02": future})
    job_id = db.submit(work, False)
    provider = fake_provider([])
    Worker(settings, db, [provider], Mock()).tick()
    job = db.job(job_id)
    assert (job["state"], job["reason"]) == ("WAITING_FOR_RELEASE", "NEXT_EPISODE_NOT_AIRED")
    assert job["next_run"] > time.time() + 3 * 86400
    provider.search.assert_not_called()


def test_unaired_episode_is_never_reported_missing(db, movie):
    work = Work(kind="series", external_id=8, title="Example", seasons=[1], episodes=["S01E01", "S01E02"])
    job_id = db.submit(work, False)
    db.transition(job_id, "AVAILABLE")
    assert db.observe_completed(db.job(job_id), {"S01E01"}, unaired=["S01E02"])
    assert db.job(job_id)["state"] == "AVAILABLE"


def test_new_air_dates_merge_into_existing_series(db):
    work = Work(kind="series", external_id=8, title="Example", seasons=[1], episodes=["S01E01"],
                air_dates={"S01E01": "2020-01-01"})
    job_id = db.submit(work, False)
    db.submit(work.model_copy(update={"episodes": ["S01E01", "S01E02"], "air_dates": {"S01E02": ""}}), False)
    stored = json.loads(db.job(job_id)["work"])
    assert stored["air_dates"] == {"S01E01": "2020-01-01", "S01E02": ""}


# ---------- manual choice


@pytest.fixture
def empty_library(monkeypatch):
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _s, _w: set())


def test_manual_choice_downloads_doubted_release(settings, db, movie, monkeypatch, empty_library):
    settings = settings.model_copy(update={"dry_run": False})
    monkeypatch.setattr("mae.worker.shutil.disk_usage", lambda _p: Mock(free=10**15))
    # No Russian audio evidence: automatic selection would only offer it for review.
    provider = fake_provider([release("Example.2024.2160p.WEB-DL.H.265", "pick")], card="")
    job_id = db.submit(movie, False)
    db.choose_release(job_id, "fixture", "pick")
    downloader = magnet_downloader()
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert job["state"] == "DOWNLOADING" and job["torrent_hash"] == "b" * 40
    assert json.loads(job["candidate"])["parsed"]["manual"] is True
    assert job["manual_release"] is None
    downloader.resume.assert_called_once()


def test_manual_choice_that_vanished_needs_review(settings, db, movie, empty_library):
    settings = settings.model_copy(update={"dry_run": False})
    job_id = db.submit(movie, False)
    db.choose_release(job_id, "fixture", "gone")
    Worker(settings, db, [fake_provider([])], Mock()).tick()
    assert (db.job(job_id)["state"], db.job(job_id)["reason"]) == ("NEEDS_REVIEW", "MANUAL_RELEASE_NOT_FOUND")


def test_choosing_discards_running_download(db, movie):
    job_id = db.submit(movie, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="c" * 40, backup_hash="d" * 40)
    db.choose_release(job_id, "fixture", "x")
    queued = {r["torrent_hash"] for r in db.execute("SELECT torrent_hash FROM discard_downloads")}
    assert queued == {"c" * 40, "d" * 40}
    assert db.job(job_id)["torrent_hash"] is None and db.job(job_id)["state"] == "REQUESTED"


def test_manual_pick_is_not_replaced_for_slowness(settings, db, movie):
    job_id = db.submit(movie, False)
    candidate = Candidate(release=release("Example.2024.1080p"), parsed={"manual": True, "screen_size": "1080p"})
    db.transition(job_id, "DOWNLOADING", torrent_hash="c" * 40, candidate=candidate.model_dump_json())
    worker = Worker(settings, db, [], Mock())
    assert worker.check_slow(db.job(job_id), {"progress": 0.1, "downloaded": 1, "eta": 10**9}, 10**6) is False


def test_choose_api_validates_candidate(settings, db, movie):
    from mae.api import create_app

    job_id = db.submit(movie, False)
    big = Candidate(release=release("Example.2024.1080p", "big"), reasons=["TOO_LARGE"]).model_dump()
    doubt = Candidate(release=release("Example.2024.1080p", "ok"), unknown=["russian_audio"]).model_dump()
    db.transition(job_id, "NEEDS_REVIEW", decision=json.dumps({"candidates": [big, doubt]}))
    settings = settings.model_copy(update={"providers": [{"name": "fixture", "url": "http://gw.test/api"}]})
    client = TestClient(create_app(settings, db, seerr=Mock()))
    headers = {"X-Api-Key": settings.api_key.get_secret_value()}
    url = f"/admin/jobs/{job_id}/choose"
    assert client.post(url, json={"provider": "fixture", "release_id": "nope"}, headers=headers).status_code == 404
    assert client.post(url, json={"provider": "fixture", "release_id": "big"}, headers=headers).status_code == 409
    response = client.post(url, json={"provider": "fixture", "release_id": "ok"}, headers=headers)
    assert response.status_code == 200 and response.json()["state"] == "REQUESTED"
    assert json.loads(db.job(job_id)["manual_release"]) == {"provider": "fixture", "release_id": "ok"}


# ---------- upgrades of library items


@pytest.fixture
def library_movie(settings, db, movie, tmp_path):
    """An available 1080p movie with a published hardlink and its seeding torrent."""
    store = tmp_path / "store"
    downloads = store / "downloads/films"
    downloads.mkdir(parents=True)
    old = downloads / "Example.2024.1080p.WEB-DL.H.264.mkv"
    old.write_bytes(b"old")
    destination = store / "films/Example (2024) [tmdbid-1]/Example (2024) [tmdbid-1].mkv"
    destination.parent.mkdir(parents=True)
    destination.hardlink_to(old)
    job_id = db.submit(movie, False)
    current = evaluate(parse_candidate(release("Example.2024.1080p.WEB-DL.H.264", "old"), ReleaseDetails(
        evidence=[Evidence(id="card", text="Аудио: русский", method="fixture")])), movie, Policy())
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40, candidate=current.model_dump_json())
    db.transition(job_id, "AVAILABLE")
    probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
    stat = destination.stat()
    db.execute("INSERT INTO imports(job_id,source,destination,state,probe,device,inode) "
               "VALUES(:id,:src,:dst,'PUBLISHED',:probe,:dev,:ino)", id=job_id, src=str(old),
               dst=str(destination), probe=json.dumps(probe), dev=stat.st_dev, ino=stat.st_ino)
    settings = settings.model_copy(update={"store": store, "dry_run": False})
    return settings, job_id, destination, downloads


def test_upgrade_check_records_better_release(db, library_movie):
    settings, job_id, _, _ = library_movie
    provider = fake_provider([
        release("Example.2024.1080p.WEB-DL.H.264", "old"),
        release("Example.2024.2160p.WEB-DL.H.265", "uhd"),
    ])
    Worker(settings, db, [provider], Mock()).tick()
    job = db.job(job_id)
    upgrade = json.loads(job["upgrade_candidate"])
    assert upgrade["candidate"]["release"]["release_id"] == "uhd"
    assert (upgrade["from"]["resolution"], upgrade["to"]["resolution"]) == (1080, 2160)
    assert job["state"] == "AVAILABLE" and job["upgrade_checked"] > 0
    assert any(e["reason"] == "UPGRADE_AVAILABLE" for e in db.execute("SELECT reason FROM events"))
    # Checked once per interval, not every tick.
    provider.search.reset_mock()
    Worker(settings, db, [provider], Mock()).tick()
    provider.search.assert_not_called()


def test_same_quality_is_not_an_upgrade(db, library_movie):
    settings, job_id, _, _ = library_movie
    Worker(settings, db, [fake_provider([release("Example.2024.1080p.WEB-DL.H.264", "other")])], Mock()).tick()
    assert db.job(job_id)["upgrade_candidate"] is None


def test_auto_upgrade_replaces_file_after_verification(db, library_movie, monkeypatch):
    settings, job_id, destination, downloads = library_movie
    settings = settings.model_copy(update={"auto_upgrade": True})
    monkeypatch.setattr("mae.worker.shutil.disk_usage", lambda _p: Mock(free=10**15))
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _s, _w: {"movie"})
    monkeypatch.setattr("mae.worker.Jellyfin.refresh", lambda _s: None)
    provider = fake_provider([release("Example.2024.2160p.WEB-DL.H.265", "uhd")])
    downloader = magnet_downloader()
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert job["replacing"] == 1 and job["state"] == "REQUESTED"
    # Seerr reports a fulfilled request as COMPLETED (5); that must not cancel the replacement.
    db.execute("INSERT INTO seerr_requests(request_id,job_id,status,active,suppressed,generation,updated) "
               "VALUES(7,:id,5,0,0,0,0)", id=job_id)
    db.observe_seerr_request(7, job_id, 5)
    assert db.job(job_id)["state"] == "REQUESTED"
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert job["state"] == "DOWNLOADING" and job["torrent_hash"] == "b" * 40
    assert destination.read_bytes() == b"old"  # untouched while downloading
    new = downloads / "Example.2024.2160p.WEB-DL.H.265.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=3840x2160:r=1", "-f", "lavfi", "-i",
                    "anullsrc", "-t", "1", "-c:v", "libx265", "-preset", "ultrafast", "-c:a", "aac",
                    "-metadata:s:a:0", "language=rus", str(new)], check=True, timeout=60)
    downloader.info.return_value = {"progress": 1, "state": "uploading", "save_path": str(downloads)}
    downloader.files.return_value = [{"name": new.name}]
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert (job["state"], job["reason"], job["replacing"]) == ("AVAILABLE", "UPGRADED", 0)
    imports = db.execute("SELECT * FROM imports WHERE job_id=:id", id=job_id)
    assert len(imports) == 1 and imports[0]["source"] == str(new)
    library = Path(imports[0]["destination"])
    assert library.stat().st_ino == new.stat().st_ino
    assert not list(destination.parent.glob("*.aiarr-replaced"))
    # The old release's torrent is queued for removal.
    assert {r["torrent_hash"] for r in db.execute("SELECT torrent_hash FROM discard_downloads")} == {"a" * 40}


def test_failed_replacement_keeps_old_file(db, library_movie, monkeypatch):
    settings, job_id, destination, downloads = library_movie
    db.choose_release(job_id, "fixture", "uhd", replacing=True)
    db.transition(job_id, "DOWNLOADING", torrent_hash="b" * 40)
    bad = downloads / "broken.mkv"
    bad.write_bytes(b"not a video")

    def rejected(*_args):
        raise ValueError("VIDEO_CODEC_MISMATCH")

    monkeypatch.setattr("mae.worker.probe", rejected)
    downloader = Mock()
    downloader.info.return_value = {"progress": 1, "state": "uploading", "save_path": str(downloads)}
    downloader.files.return_value = [{"name": bad.name}]
    Worker(settings, db, [], downloader).tick()
    assert db.job(job_id)["state"] == "NEEDS_REVIEW"
    assert destination.read_bytes() == b"old"
    assert db.abort_replacement(job_id)
    job = db.job(job_id)
    assert (job["state"], job["replacing"], job["reason"]) == ("AVAILABLE", 0, "UPGRADE_CANCELLED")
    assert json.loads(job["candidate"])["release"]["release_id"] == "old"
    assert "b" * 40 in {r["torrent_hash"] for r in db.execute("SELECT torrent_hash FROM discard_downloads")}


# ---------- source health


def test_source_requests_are_journaled_and_reported(settings, db, movie):
    from mae.api import create_app

    provider = fake_provider([release("Example.2024.1080p.WEB-DL.H.264")])
    db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    stages = {(r["stage"], r["outcome"]) for r in db.execute("SELECT stage,outcome FROM provider_stats")}
    assert stages == {("SEARCHING", "OK"), ("ENRICHING", "OK")}
    client = TestClient(create_app(settings, db, seerr=Mock()))
    stats = client.get("/admin/provider-stats", headers={"X-Api-Key": settings.api_key.get_secret_value()}).json()
    assert stats[0]["provider"] == "fixture" and stats[0]["searches"] == 1 and stats[0]["fit"] == 1
    assert stats[0]["last_search_outcome"] == "OK"


# ---------- speed schedule


@pytest.mark.parametrize("start,end,hour,limited", [
    (8, 1, 12, True), (8, 1, 0, True), (8, 1, 3, False), (1, 7, 3, True), (1, 7, 9, False), (5, 5, 5, False),
])
def test_speed_window(settings, db, start, end, hour, limited):
    settings = settings.model_copy(update={"speed_limited_from_hour": start, "speed_limited_to_hour": end,
                                           "utc_offset_hours": 3})
    utc = (hour - 3) % 24 * 3600
    assert Worker(settings, db, [], Mock()).speed_limited_now(utc) is limited


def test_speed_schedule_applies_and_lifts_once(settings, db):
    settings = settings.model_copy(update={"speed_schedule_enabled": True, "speed_limited_from_hour": 0,
                                           "speed_limited_to_hour": 0})
    downloader = Mock()
    Worker(settings, db, [], downloader).tick()
    downloader.apply_speed_limits.assert_called_once_with(False, settings.speed_limit_download,
                                                          settings.speed_limit_upload)
    off = settings.model_copy(update={"speed_schedule_enabled": False})
    downloader = Mock()
    downloader.speed_limited.return_value = True
    Worker(off, db, [], downloader).tick()
    downloader.client.transfer_set_speed_limits_mode.assert_called_once_with(intended_state=False)
    downloader = Mock()
    Worker(off, db, [], downloader).tick()
    downloader.speed_limited.assert_not_called()


# ---------- seeding cleanup


def test_cleanup_removes_old_torrent_only_with_intact_library_file(db, library_movie):
    settings, job_id, destination, downloads = library_movie
    settings = settings.model_copy(update={"cleanup_after_days": 3, "upgrade_check_hours": 0})
    downloader = Mock()
    downloader.info.return_value = {"progress": 1, "save_path": str(downloads),
                                    "completion_on": time.time() - 4 * 86400}
    downloader.files.return_value = [{"name": "Example.2024.1080p.WEB-DL.H.264.mkv"}]
    destination.unlink()
    destination.write_bytes(b"replaced by hand")
    Worker(settings, db, [], downloader).tick()
    downloader.delete_owned.assert_not_called()
    destination.unlink()
    destination.hardlink_to(downloads / "Example.2024.1080p.WEB-DL.H.264.mkv")
    db.execute("UPDATE imports SET inode=:ino", ino=destination.stat().st_ino)
    Worker(settings, db, [], downloader).tick()
    downloader.delete_owned.assert_called_once()
    job = db.job(job_id)
    assert job["torrent_hash"] is None and job["state"] == "AVAILABLE"
    assert not db.execute("SELECT 1 FROM job_downloads WHERE job_id=:id", id=job_id)


def test_recent_torrent_is_kept(db, library_movie):
    settings, _, _, downloads = library_movie
    settings = settings.model_copy(update={"cleanup_after_days": 3, "upgrade_check_hours": 0})
    downloader = Mock()
    downloader.info.return_value = {"progress": 1, "save_path": str(downloads), "completion_on": time.time()}
    Worker(settings, db, [], downloader).tick()
    downloader.delete_owned.assert_not_called()
