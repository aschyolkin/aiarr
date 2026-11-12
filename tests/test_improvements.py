"""Journal dedupe, card cache, parallel sources, verification fallback, series upgrades, text search."""
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from mae.api import create_app
from mae.config import Policy, ProviderConfig
from mae.deepseek import Verdict, validate_verdict
from mae.domain import DownloadHandle, Evidence, ReleaseDetails, SearchResult, Work
from mae.parsing import parse_candidate
from mae.policies import evaluate, score_of
from mae.providers import TorznabProvider
from mae.seerr import series_id
from mae.worker import Worker


def release(title, rid="r1", provider="fixture", size=4 * 1024**3, seeders=10, infohash=None):
    return SearchResult(provider=provider, release_id=rid, title=title, size=size, seeders=seeders,
                        infohash=infohash)


def fake_provider(results, name="fixture"):
    provider = Mock()
    provider.config.name = name
    provider.search.return_value = results
    provider.fetch_details.return_value = ReleaseDetails(
        evidence=[Evidence(id="card", text="Аудио: русский", method="fixture")]
    )
    provider.resolve_download.return_value = DownloadHandle(url="magnet:?xt=urn:btih:" + "b" * 40)
    return provider


def candidate_json(title, rid, work):
    details = ReleaseDetails(evidence=[Evidence(id="card", text="Аудио: русский", method="fixture")])
    return evaluate(parse_candidate(release(title, rid), details), work, Policy()).model_dump_json()


def test_repeated_download_check_is_journaled_once(db, movie):
    job_id = db.submit(movie, False)
    for _ in range(5):
        db.transition(job_id, "DOWNLOADING", delay=30)
    db.transition(job_id, "DOWNLOADING", "BACKUP_PROMOTED")
    db.transition(job_id, "DOWNLOADING", delay=30)
    events = [(e["state"], e["reason"]) for e in db.execute("SELECT state,reason FROM events ORDER BY id")]
    assert events == [("DOWNLOADING", None), ("DOWNLOADING", "BACKUP_PROMOTED"), ("DOWNLOADING", None)]


def test_unchanged_series_request_keeps_stored_work(db):
    work = Work(kind="series", external_id=8, title="Example", seasons=[1], episodes=["S01E01"],
                air_dates={"S01E01": "2020-01-01"})
    job_id = db.submit(work, False)
    db.set_poster(job_id, "/poster.jpg")
    before = db.job(job_id)["work"]
    assert db.submit(work.model_copy(update={"poster_path": "/poster.jpg"}), False) == job_id
    assert db.job(job_id)["work"] == before
    assert len(db.execute("SELECT * FROM episodes")) == 1


def test_release_card_is_fetched_once_within_cache_window(settings, db, movie):
    provider = fake_provider([release("Example.2024.1080p.WEB-DL.H.264")])
    job_id = db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    db.transition(job_id, "REQUESTED")
    Worker(settings, db, [provider], Mock()).tick()
    assert provider.search.call_count == 2 and provider.fetch_details.call_count == 1
    assert db.job(job_id)["reason"] == "DRY_RUN_SELECTED"
    uncached = settings.model_copy(update={"card_cache_hours": 0})
    db.transition(job_id, "REQUESTED")
    Worker(uncached, db, [provider], Mock()).tick()
    assert provider.fetch_details.call_count == 2


def test_sources_are_searched_in_parallel_and_merged_in_order(settings, db, movie):
    barrier_times = []

    def slow(_work):
        barrier_times.append(time.monotonic())
        time.sleep(0.3)
        return [release("Example.2024.1080p.WEB-DL.H.264", "a", "first")]

    first, second = fake_provider([], "first"), fake_provider(
        [release("Example.2024.2160p.WEB-DL.H.265", "b", "second")], "second")
    first.search.side_effect = slow
    job_id = db.submit(movie)
    started = time.monotonic()
    Worker(settings, db, [first, second], Mock()).tick()
    job = db.job(job_id)
    providers = [c["release"]["provider"] for c in json.loads(job["decision"])["candidates"]]
    assert providers == ["first", "second"]
    assert json.loads(job["candidate"])["release"]["release_id"] == "b"
    assert second.search.called and time.monotonic() - started < 5


def test_same_torrent_from_two_sources_is_one_candidate(settings, db, movie):
    one = fake_provider([release("Example.2024.1080p.WEB-DL.H.264", "a", "one", seeders=3, infohash="c" * 40)], "one")
    two = fake_provider([release("Example.2024.1080p.WEB-DL.H.264", "b", "two", seeders=9, infohash="c" * 40)], "two")
    worker = Worker(settings, db, [one, two], Mock())
    job = db.job(db.submit(movie))
    _, _, eligible, _ = worker.search_candidates(job, movie, use_llm=False, progress=False)
    assert [(c.release.provider, c.release.seeders) for _, c, _ in eligible] == [("two", 9)]


def test_failed_verification_moves_on_to_another_release(settings, db, movie, tmp_path, monkeypatch):
    store = tmp_path / "store"
    downloads = store / "downloads/films"
    downloads.mkdir(parents=True)
    (downloads / "bad.mkv").write_bytes(b"x")
    settings = settings.model_copy(update={"store": store, "dry_run": False})
    job_id = db.submit(movie, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40,
                  candidate=candidate_json("Example.2024.1080p.WEB-DL.H.264", "bad", movie))

    def no_russian(*_args):
        raise ValueError("RUSSIAN_AUDIO_NOT_TAGGED")

    monkeypatch.setattr("mae.worker.probe", no_russian)
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _s, _w: set())
    downloader = Mock()
    downloader.info.return_value = {"progress": 1, "state": "uploading", "save_path": str(downloads)}
    downloader.files.return_value = [{"name": "bad.mkv"}]
    Worker(settings, db, [], downloader).tick()
    job = db.job(job_id)
    assert (job["state"], job["reason"], job["torrent_hash"]) == ("RETRY_SCHEDULED", "RUSSIAN_AUDIO_NOT_TAGGED", None)
    downloader.delete_owned.assert_called_once_with("a" * 40, store / "downloads")
    # The next search never offers the rejected release, also under another source's name.
    provider = fake_provider([
        release("Example.2024.1080p.WEB-DL.H.264", "bad"),
        release("Example.2024.1080p.WEB-DL.H.264", "mirror", infohash="a" * 40),
        release("Example.2024.1080p.WEB-DL.H.264", "good", seeders=1),
    ])
    worker = Worker(settings, db, [provider], downloader)
    decisions, _, eligible, _ = worker.search_candidates(job, movie, use_llm=False, progress=False)
    assert [c.release.release_id for _, c, _ in eligible] == ["good"]
    assert sum("VERIFICATION_FAILED" in d["reasons"] for d in decisions) == 2


def verification_fails(settings, db, movie, tmp_path, monkeypatch, candidate):
    store = tmp_path / "store"
    downloads = store / "downloads/films"
    downloads.mkdir(parents=True)
    (downloads / "bad.mkv").write_bytes(b"x")
    settings = settings.model_copy(update={"store": store, "dry_run": False, "max_attempts": 1})
    job_id = db.submit(movie, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40, candidate=candidate)
    monkeypatch.setattr("mae.worker.probe", Mock(side_effect=ValueError("VIDEO_CODEC_MISMATCH")))
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _s, _w: set())
    downloader = Mock()
    downloader.info.return_value = {"progress": 1, "state": "uploading", "save_path": str(downloads)}
    downloader.files.return_value = [{"name": "bad.mkv"}]
    Worker(settings, db, [], downloader).tick()
    return db.job(job_id), downloader


def test_manual_choice_stays_for_review(settings, db, movie, tmp_path, monkeypatch):
    chosen = json.loads(candidate_json("Example.2024.1080p.WEB-DL.H.264", "bad", movie))
    chosen["parsed"]["manual"] = True
    job, downloader = verification_fails(settings, db, movie, tmp_path, monkeypatch, json.dumps(chosen))
    assert (job["state"], job["reason"], job["torrent_hash"]) == ("NEEDS_REVIEW", "VIDEO_CODEC_MISMATCH", "a" * 40)
    downloader.delete_owned.assert_not_called()


def test_last_attempt_waits_for_new_releases_instead_of_review(settings, db, movie, tmp_path, monkeypatch):
    job, _ = verification_fails(settings, db, movie, tmp_path, monkeypatch,
                                candidate_json("Example.2024.1080p.WEB-DL.H.264", "bad", movie))
    assert (job["state"], job["reason"], job["torrent_hash"]) == ("WAITING_FOR_RELEASE", "VIDEO_CODEC_MISMATCH", None)
    assert db.execute("SELECT release_id FROM rejected_releases")[0]["release_id"] == "bad"


def unproven(movie, rid="1"):
    details = ReleaseDetails(evidence=[Evidence(id="card", text="Описание без языка", method="fixture")])
    c = evaluate(parse_candidate(release("Example.2024.1080p.WEB-DL.H.264", rid), details), movie, Policy())
    assert c.unknown and not c.reasons
    return (10, c.model_copy(update={"score": score_of(c, Policy())}), None)


def test_judge_follows_the_ai_verdict_and_gives_a_reason(settings, db, movie, monkeypatch):
    job = db.job(db.submit(movie, False))
    worker = Worker(settings, db, [], Mock())
    items = [unproven(movie)]
    verdict = Verdict(download=False, choice=None, reason_ru="Похоже на другую картину.", reason_en="Looks like another film.")
    monkeypatch.setattr("mae.worker.DeepSeek.decide", lambda *_a, **_k: verdict)
    eligible, result = worker.judge(job, movie, items)
    assert eligible == [] and result["by"] == "deepseek" and not result["download"]
    assert result["reason_en"] == "Looks like another film."
    verdict = Verdict(download=True, choice=0, reason_ru="Подходит.", reason_en="Fits.")
    monkeypatch.setattr("mae.worker.DeepSeek.decide", lambda *_a, **_k: verdict)
    eligible, result = worker.judge(job, movie, items)
    assert eligible == items and result["download"] and result["release"].startswith("Example")


def test_judge_falls_back_to_the_best_score_without_ai(settings, db, movie, monkeypatch):
    job = db.job(db.submit(movie, False))
    monkeypatch.setattr("mae.worker.DeepSeek.decide", lambda *_a, **_k: None)
    eligible, result = Worker(settings, db, [], Mock()).judge(job, movie, [unproven(movie)])
    assert len(eligible) == 1 and result["by"] == "rules" and result["download"] and result["reason_ru"]


def test_verdict_validation():
    assert validate_verdict('{"download":true,"choice":1,"reason_ru":"да","reason_en":"yes"}', 2).choice == 1
    for raw in ('{"download":true,"choice":null,"reason_ru":"да","reason_en":"yes"}',
                '{"download":false,"choice":0,"reason_ru":"нет","reason_en":"no"}',
                '{"download":true,"choice":5,"reason_ru":"да","reason_en":"yes"}',
                '{"download":false,"choice":null,"reason_ru":"","reason_en":"no"}'):
        with pytest.raises(ValueError):
            validate_verdict(raw, 2)


def test_torznab_reads_infohash_and_searches_by_id_when_declared(respx_mock, movie):
    config = ProviderConfig(name="fixture", url="http://gateway.test/api", trusted_gateway=True, interval=0,
                            categories=[2000])
    caps = '<caps><searching><movie-search available="yes" supportedParams="q,tmdbid"/></searching></caps>'
    item = """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item><title>%s</title>
    <guid>%s</guid><enclosure url="http://gateway.test/file" length="1000"/>
    <torznab:attr name="infohash" value="%s"/></item></channel></rss>"""

    def answer(request):
        kind = request.url.params.get("t")
        if kind == "caps":
            assert "cat" not in request.url.params
            return httpx.Response(200, text=caps)
        assert request.url.params.get("cat") == "2000"
        if kind == "movie":
            assert request.url.params.get("tmdbid") == "1"
            return httpx.Response(200, text=item % ("Renamed.2024.1080p", "by-id", "D" * 40))
        return httpx.Response(200, text=item % ("Example.2024.1080p", "by-title", "e" * 40))

    respx_mock.get(config.url).mock(side_effect=answer)
    provider = TorznabProvider(config)
    assert [r.infohash for r in provider.search(movie)] == ["e" * 40]
    assert [(r.title, r.infohash) for r in provider.search_by_id(movie)] == [("Renamed.2024.1080p", "d" * 40)]
    worker = Worker.__new__(Worker)
    worker.db = Mock()
    assert [r.title for r in worker.search_all(provider, movie)] == ["Example.2024.1080p", "Renamed.2024.1080p"]
    series = Work(kind="series", external_id=8, title="Example", seasons=[1])
    assert provider.search_by_id(series) == []  # tv-search is not declared


def test_cleanup_by_share_ratio(settings, db, movie, tmp_path):
    store = tmp_path / "store"
    downloads = store / "downloads/films"
    downloads.mkdir(parents=True)
    source = downloads / "Example.mkv"
    source.write_bytes(b"x")
    destination = store / "films/Example.mkv"
    destination.parent.mkdir(parents=True)
    destination.hardlink_to(source)
    job_id = db.submit(movie, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    db.transition(job_id, "AVAILABLE")
    stat = destination.stat()
    db.execute("INSERT INTO imports(job_id,source,destination,state,probe,device,inode) "
               "VALUES(:id,:src,:dst,'PUBLISHED','{}',:dev,:ino)", id=job_id, src=str(source), dst=str(destination),
               dev=stat.st_dev, ino=stat.st_ino)
    settings = settings.model_copy(update={"store": store, "dry_run": False, "cleanup_ratio": 2.0,
                                           "upgrade_check_hours": 0})
    downloader = Mock()
    downloader.files.return_value = [{"name": "Example.mkv"}]
    downloader.info.return_value = {"progress": 1, "save_path": str(downloads), "completion_on": time.time(),
                                    "ratio": 1.5}
    Worker(settings, db, [], downloader).tick()
    downloader.delete_owned.assert_not_called()
    downloader.info.return_value["ratio"] = 2.1
    Worker(settings, db, [], downloader).tick()
    downloader.delete_owned.assert_called_once()
    assert destination.exists()


def series_library(settings, db, tmp_path):
    """An available two-episode 1080p season imported by aiarr."""
    store = tmp_path / "store"
    downloads = store / "downloads/serials"
    downloads.mkdir(parents=True)
    work = Work(kind="series", external_id=8, title="Example", seasons=[1], episodes=["S01E01", "S01E02"])
    job_id = db.submit(work, False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)
    db.transition(job_id, "AVAILABLE")
    probe = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
    season = store / "serials/Example [tvdbid-8]/Season 01"
    season.mkdir(parents=True)
    for key in work.episodes:
        source = downloads / f"Example.{key}.1080p.mkv"
        source.write_bytes(key.encode())
        destination = season / f"Example - {key}.mkv"
        destination.hardlink_to(source)
        stat = destination.stat()
        db.execute("INSERT INTO imports(job_id,source,destination,episode,state,probe,device,inode) "
                   "VALUES(:id,:src,:dst,:ep,'PUBLISHED',:probe,:dev,:ino)", id=job_id, src=str(source),
                   dst=str(destination), ep=key, probe=json.dumps(probe), dev=stat.st_dev, ino=stat.st_ino)
    settings = settings.model_copy(update={"store": store, "dry_run": False})
    return settings, job_id, work, season, downloads


def test_series_upgrade_is_offered_by_resolution(settings, db, tmp_path):
    settings, job_id, _, _, _ = series_library(settings, db, tmp_path)
    provider = fake_provider([
        release("Example.S01.1080p.WEB-DL.H.264", "same"),
        release("Example.S01.2160p.WEB-DL.H.265", "uhd"),
        release("Example.S02.2160p.WEB-DL.H.265", "other-season"),
    ])
    Worker(settings, db, [provider], Mock()).tick()
    upgrade = json.loads(db.job(job_id)["upgrade_candidate"])
    assert upgrade["candidate"]["release"]["release_id"] == "uhd"
    assert (upgrade["from"]["resolution"], upgrade["to"]["resolution"]) == (1080, 2160)


def test_series_upgrade_replaces_only_the_episodes_it_brings(settings, db, tmp_path, monkeypatch):
    settings, job_id, work, season, downloads = series_library(settings, db, tmp_path)
    monkeypatch.setattr("mae.worker.shutil.disk_usage", lambda _p: Mock(free=10**15))
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _s, _w: {"S01E01", "S01E02"})
    monkeypatch.setattr("mae.worker.Jellyfin.refresh", lambda _s: None)
    monkeypatch.setattr("mae.worker.Jellyfin.existing_series_folder", lambda _s, _w: None)
    provider = fake_provider([release("Example.S01.2160p.WEB-DL.H.265", "uhd")])
    name = "Example.S01E01.2160p.WEB-DL.H.265.mkv"
    downloader = Mock()
    downloader.magnet_preflight.return_value = ("b" * 40, 1024, [(name, 1024)])
    db.choose_release(job_id, "fixture", "uhd", replacing=True)
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert job["state"] == "DOWNLOADING" and job["torrent_hash"] == "b" * 40
    assert json.loads(job["candidate"])["parsed"]["download_episodes"] == ["S01E01", "S01E02"]
    new = downloads / name
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=3840x2160:r=1", "-f", "lavfi", "-i",
                    "anullsrc", "-t", "1", "-c:v", "libx265", "-preset", "ultrafast", "-c:a", "aac",
                    "-metadata:s:a:0", "language=rus", str(new)], check=True, timeout=60)
    old_first = downloads / "Example.S01E01.1080p.mkv"
    old_second = downloads / "Example.S01E02.1080p.mkv"

    def info(torrent_hash):
        return {"progress": 1, "state": "uploading", "save_path": str(downloads), "hash": torrent_hash}

    downloader.info.side_effect = info
    # The old torrent still backs episode 2, which the new release does not bring.
    downloader.files.side_effect = lambda torrent_hash: (
        [{"name": name}] if torrent_hash == "b" * 40 else [{"name": old_first.name}, {"name": old_second.name}]
    )
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert (job["state"], job["reason"], job["replacing"]) == ("AVAILABLE", "UPGRADED", 0)
    imports = {r["episode"]: r for r in db.execute("SELECT * FROM imports WHERE job_id=:id", id=job_id)}
    assert imports["S01E01"]["source"] == str(new) and imports["S01E02"]["source"] == str(old_second)
    assert Path(imports["S01E01"]["destination"]).stat().st_ino == new.stat().st_ino
    assert (season / "Example - S01E02.mkv").stat().st_ino == old_second.stat().st_ino
    assert not list(season.glob("*.aiarr-replaced"))
    assert not db.execute("SELECT * FROM discard_downloads")


def client_for(settings, db):
    settings = settings.model_copy(update={"providers": [{"name": "fixture", "url": "http://gw.test/api"}]})
    return TestClient(create_app(settings, db, seerr=Mock()),
                      headers={"X-Api-Key": settings.api_key.get_secret_value()})


def test_text_search_lists_releases_and_choice_keeps_the_query(settings, db, movie, monkeypatch):
    job_id = db.submit(movie, False)
    seen = []

    def provider(_config):
        fake = Mock()
        fake.search.side_effect = lambda query: seen.append((query.title, query.year)) or [
            release("Пример / Example (2024) WEB-DL 1080p", "found")]
        return fake

    monkeypatch.setattr("mae.providers.make_provider", provider)
    client = client_for(settings, db)
    data = client.post(f"/admin/jobs/{job_id}/search", json={"query": " Пример 2024 "}).json()
    assert seen == [("Пример 2024", None)]
    assert [c["release"]["release_id"] for c in data["candidates"]] == ["found"]
    assert "evidence" not in data["candidates"][0]
    url = f"/admin/jobs/{job_id}/choose"
    assert client.post(url, json={"provider": "fixture", "release_id": "found"}).status_code == 404
    chosen = client.post(url, json={"provider": "fixture", "release_id": "found", "query": "Пример 2024"})
    assert chosen.status_code == 200
    assert json.loads(db.job(job_id)["manual_release"]) == {
        "provider": "fixture", "release_id": "found", "query": "Пример 2024"}


def test_manual_choice_is_found_again_by_its_query(settings, db, movie, monkeypatch):
    monkeypatch.setattr("mae.worker.shutil.disk_usage", lambda _p: Mock(free=10**15))
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _s, _w: set())
    settings = settings.model_copy(update={"dry_run": False})
    job_id = db.submit(movie, False)
    provider = fake_provider([])
    provider.search.side_effect = lambda query: (
        [release("Example.2024.1080p.WEB-DL.H.264", "found")] if query.title == "my words" else [])
    downloader = Mock()
    downloader.magnet_preflight.return_value = ("b" * 40, 1024, [("Example.mkv", 1024)])
    db.choose_release(job_id, "fixture", "found", query="my words")
    Worker(settings, db, [provider], downloader).tick()
    job = db.job(job_id)
    assert job["state"] == "DOWNLOADING" and json.loads(job["candidate"])["parsed"]["manual"] is True


def test_calendar_lists_monitored_episodes_by_air_date(settings, db):
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + settings.utc_offset_hours * 3600))
    soon = time.strftime("%Y-%m-%d", time.gmtime(time.time() + settings.utc_offset_hours * 3600 + 3 * 86400))
    work = Work(kind="series", external_id=8, title="Example", seasons=[1],
                episodes=["S01E01", "S01E02", "S01E03", "S01E04"],
                air_dates={"S01E01": "2001-01-01", "S01E02": today, "S01E03": soon, "S01E04": ""})
    job_id = db.submit(work, False)
    db.record_coverage(job_id, {"S01E02"})
    data = client_for(settings, db).get("/admin/calendar").json()
    assert [(e["episode"], e["available"], e["aired"]) for e in data["episodes"]] == [
        ("S01E02", True, True), ("S01E03", False, False)]


def test_unusable_torrent_is_skipped_not_sent_to_review(settings, db, movie, monkeypatch):
    job_id = db.submit(movie, False)
    db.transition(job_id, "SELECTED", candidate=candidate_json("Example.2024.1080p.WEB-DL.H.264", "pack", movie))
    worker = Worker(settings, db, [], Mock())
    monkeypatch.setattr(worker, "process", Mock(side_effect=ValueError("TORRENT_EPISODE_MAPPING_UNVERIFIED")))
    worker.run_job(db.job(job_id))
    job = db.job(job_id)
    assert (job["state"], job["reason"], job["candidate"]) == ("RETRY_SCHEDULED", "TORRENT_EPISODE_MAPPING_UNVERIFIED", None)
    assert db.execute("SELECT release_id FROM rejected_releases")[0]["release_id"] == "pack"


def test_ai_choice_is_marked_until_the_administrator_acknowledges(settings, db, movie, monkeypatch):
    job_id = db.submit(movie, False)
    verdict = {"download": True, "by": "deepseek", "release": "X", "reason_ru": "да", "reason_en": "yes"}
    db.transition(job_id, "DOWNLOADING", decision=json.dumps({"candidates": [], "verdict": verdict}),
                  candidate=candidate_json("Example.2024.1080p.WEB-DL.H.264", "ai", movie))
    client = client_for(settings, db)

    def pending():
        return [j["ai_pending"] for j in client.get("/admin/overview").json()["jobs"] if j["id"] == job_id]

    assert pending() == [1]
    assert client.post(f"/admin/jobs/{job_id}/ack-ai", headers={"X-Aiarr-UI": "1"}).status_code == 200
    assert pending() == [None] or pending() == [0]
    assert json.loads(db.job(job_id)["decision"])["verdict"]["reviewed"] is True


def test_series_without_tvdb_id_is_matched_by_name_and_kept_by_tmdb_id(settings, db):
    seerr = Mock()
    seerr.get.return_value = {"results": [
        {"mediaType": "tv", "id": 477295, "name": "Где лифт?", "firstAirDate": "2024-05-01", "posterPath": "/p.jpg"},
        {"mediaType": "movie", "id": 5, "title": "Film"},
    ]}
    client = TestClient(create_app(settings, db, seerr=seerr),
                        headers={"X-Api-Key": settings.api_key.get_secret_value()})
    found = client.get("/sonarr/api/v3/series/lookup", params={"term": "Where is the elevator"}).json()
    assert [(i["title"], i["tvdbId"], i["year"]) for i in found] == [("Где лифт?", 1_000_477_295, 2024)]
    assert found[0]["remotePoster"].endswith("/p.jpg")
    assert series_id({"tmdbId": 477295, "tvdbId": None}) == 1_000_477_295
    assert series_id({"tmdbId": 477295, "tvdbId": 99}) == 99
    work = Work(kind="series", external_id=1_000_477_295, title="Где лифт?", year=2024)
    assert (work.library_namespace, work.library_id) == ("tmdbid", 477295)
    assert Work(kind="series", external_id=99, title="x").library_namespace == "tvdbid"
    assert Work(kind="movie", external_id=5, title="x").library_namespace == "tmdbid"
