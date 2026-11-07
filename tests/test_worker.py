import json
import time
from unittest.mock import Mock

import httpx

from mae.config import ProviderConfig
from mae.db import Database
from mae.domain import DownloadHandle, Evidence, Outcome, ProviderError, ReleaseDetails, SearchResult, Work
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


def test_dry_run_selects_pack_only_for_missing_episode(settings, db, monkeypatch):
    settings = settings.model_copy(update={"jellyfin_key": settings.api_key})
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _self, _work: {"S01E01"})
    provider = Mock()
    provider.config.name = "fixture"
    provider.search.return_value = [SearchResult(
        provider="fixture", release_id="pack", title="Example.S01E01-E02.1080p.WEB-DL.H.264",
        size=1000,
    )]
    provider.fetch_details.return_value = ReleaseDetails(evidence=[
        Evidence(id="card", text="Audio #1: Russian", method="fixture")
    ])
    downloader = Mock()
    job_id = db.submit(Work(kind="series", external_id=9, title="Example", seasons=[1],
                            episodes=["S01E01", "S01E02"]), True)

    Worker(settings, db, [provider], downloader).tick()

    job = db.job(job_id)
    assert job["reason"] == "DRY_RUN_SELECTED"
    assert json.loads(job["candidate"])["parsed"]["download_episodes"] == ["S01E02"]
    downloader.add.assert_not_called()


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



def _provider(*release_ids):
    provider = Mock()
    provider.config.name = "fixture"
    provider.config.protocol = "torznab"
    provider.search.return_value = [
        SearchResult(provider="fixture", release_id=rid, title="Example.2024.1080p.WEB-DL.H.264", size=1000)
        for rid in release_ids
    ]
    provider.fetch_details.return_value = ReleaseDetails(evidence=[
        Evidence(id="card", text="Audio #1: Russian", method="fixture")
    ])
    provider.resolve_download.side_effect = lambda release: DownloadHandle(url=f"http://gw.test/{release.release_id}")
    provider.http.get.side_effect = lambda url, **_: url.rsplit("/", 1)[1].encode()
    return provider


def _live(settings, monkeypatch):
    monkeypatch.setattr("mae.worker.Jellyfin.coverage", lambda _self, _work: set())
    monkeypatch.setattr("mae.worker.shutil.disk_usage", lambda _path: Mock(free=10**15))
    monkeypatch.setattr("mae.worker.torrent_metadata", lambda raw, **_: (raw.decode().ljust(40, "0"), 1000, [("a.mkv", 1000)]))
    return settings.model_copy(update={"dry_run": False})


def _h(name):
    return name.ljust(40, "0")


def _racing(db, movie, primary="first", backup="second"):
    job_id = db.submit(movie)
    db.transition(
        job_id, "DOWNLOADING", torrent_hash=_h(primary), backup_hash=_h(backup),
        candidate=json.dumps({"release": {"provider": "fixture", "release_id": primary}}),
        backup_candidate=json.dumps({"release": {"provider": "fixture", "release_id": backup}}),
    )
    return job_id


def _torrent(**overrides):
    return {"category": "mae", "tags": "media-acquisition-engine", "progress": 0, "downloaded": 0,
            "state": "stalledDL", "added_on": time.time() - 181, "size": 1000, "num_seeds": 5, "num_complete": 5,
            "amount_left": 1000, "eta": 60, **overrides}


FAST = 10**8


def _downloader(torrents):
    downloader = Mock()
    downloader.info.side_effect = lambda torrent_hash: torrents.get(torrent_hash)
    return downloader


def test_best_two_candidates_start_together_with_slow_backup(settings, db, movie, monkeypatch):
    settings = _live(settings, monkeypatch)
    job_id = db.submit(movie, False)
    downloader = Mock()

    Worker(settings, db, [_provider("first", "second", "third")], downloader).tick()

    job = db.job(job_id)
    assert (job["torrent_hash"], job["backup_hash"]) == (_h("first"), _h("second"))
    assert json.loads(job["backup_candidate"])["release"]["release_id"] == "second"
    calls = [(c.args[1], c.kwargs["download_limit"]) for c in downloader.add.call_args_list]
    assert calls == [(_h("first"), None), (_h("second"), settings.backup_download_limit)]


def test_backup_is_dropped_when_primary_downloads(settings, db, movie):
    job_id = _racing(db, movie)
    downloader = _downloader({_h("first"): _torrent(downloaded=FAST), _h("second"): _torrent()})

    Worker(settings, db, [], downloader).tick()

    downloader.delete_owned.assert_called_once_with(_h("second"), settings.store / "downloads")
    job = db.job(job_id)
    assert (job["state"], job["torrent_hash"], job["backup_hash"]) == ("DOWNLOADING", _h("first"), None)
    assert not db.execute("SELECT 1 FROM stalled_releases")


def test_backup_is_promoted_when_only_it_downloads(settings, db, movie):
    job_id = _racing(db, movie)
    downloader = _downloader({_h("first"): _torrent(), _h("second"): _torrent(downloaded=1, size=777)})

    Worker(settings, db, [], downloader).tick()

    downloader.set_download_limit.assert_called_once_with(_h("second"), -1)
    downloader.delete_owned.assert_called_once_with(_h("first"), settings.store / "downloads")
    job = db.job(job_id)
    assert (job["reason"], job["torrent_hash"], job["backup_hash"], job["reserved"]) == (
        "BACKUP_PROMOTED", _h("second"), None, 777)
    assert json.loads(job["candidate"])["release"]["release_id"] == "second"
    assert [r["release_id"] for r in db.execute("SELECT release_id FROM stalled_releases")] == ["first"]


def test_both_stalled_switch_to_next_two_candidates(settings, db, movie):
    job_id = _racing(db, movie)
    downloader = _downloader({_h("first"): _torrent(), _h("second"): _torrent()})
    worker = Worker(settings, db, [_provider("first", "second", "third", "fourth")], downloader)

    worker.tick()

    assert {c.args[0] for c in downloader.delete_owned.call_args_list} == {_h("first"), _h("second")}
    job = db.job(job_id)
    assert (job["state"], job["reason"], job["torrent_hash"], job["backup_hash"]) == (
        "RETRY_SCHEDULED", "DOWNLOAD_STALLED_NO_PEERS", None, None)
    assert not db.execute("SELECT 1 FROM discard_downloads")
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)

    worker.tick()

    job = db.job(job_id)
    assert job["reason"] == "DRY_RUN_SELECTED"
    assert json.loads(job["candidate"])["release"]["release_id"] == "third"
    skipped = {c["release"]["release_id"]: c["reasons"] for c in json.loads(job["decision"])["candidates"]}
    assert skipped["first"] == skipped["second"] == ["DOWNLOAD_STALLED_NO_PEERS"]


def test_race_waits_while_young_or_paused(settings, db, movie):
    for torrent in (_torrent(added_on=time.time() - 60), _torrent(state="stoppedDL")):
        job_id = _racing(db, movie)
        downloader = _downloader({_h("first"): torrent, _h("second"): _torrent(downloaded=1)})
        Worker(settings, db, [], downloader).tick()
        downloader.delete_owned.assert_not_called()
        job = db.job(job_id)
        assert (job["state"], job["torrent_hash"], job["backup_hash"]) == ("DOWNLOADING", _h("first"), _h("second"))
        db.execute("DELETE FROM job_downloads")
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM jobs")


def test_stall_threshold_is_configurable(settings, db, movie):
    job_id = _racing(db, movie)
    downloader = _downloader({_h("first"): _torrent(added_on=time.time() - 120), _h("second"): _torrent()})
    Worker(settings.model_copy(update={"stall_seconds": 60}), db, [], downloader).tick()
    assert db.job(job_id)["reason"] == "DOWNLOAD_STALLED_NO_PEERS"


def test_backup_of_a_cancelled_job_is_removed(settings, db, movie):
    job_id = _racing(db, movie)
    db.cancel_from_admin(job_id)
    downloader = _downloader({_h("first"): _torrent(), _h("second"): _torrent()})

    Worker(settings, db, [], downloader).tick()

    assert _h("second") in {c.args[0] for c in downloader.delete_owned.call_args_list}
    assert db.job(job_id)["backup_hash"] is None


def test_failed_discard_is_retried_next_tick(settings, db, movie):
    job_id = _racing(db, movie)
    downloader = _downloader({_h("first"): _torrent(downloaded=FAST), _h("second"): _torrent()})
    downloader.delete_owned.side_effect = [ProviderError(Outcome.TEMPORARY_FAILURE), True]
    worker = Worker(settings, db, [], downloader)

    worker.tick()
    assert db.job(job_id)["backup_hash"] is None
    assert db.execute("SELECT 1 FROM discard_downloads")

    worker.tick()
    assert not db.execute("SELECT 1 FROM discard_downloads")


def test_all_stalled_candidates_wait_and_are_retried_later(settings, db, movie):
    job_id = db.submit(movie)
    for rid in ("first", "second"):
        db.execute("INSERT INTO stalled_releases VALUES(:id,'fixture',:rid,NULL,0,0)", id=job_id, rid=rid)

    Worker(settings, db, [_provider("first", "second")], Mock()).tick()

    assert (db.job(job_id)["state"], db.job(job_id)["reason"]) == ("WAITING_FOR_RELEASE", "NO_PROVEN_CANDIDATE")
    assert not db.execute("SELECT 1 FROM stalled_releases WHERE job_id=:id", id=job_id)


def test_magnet_backup_is_limited_before_it_resumes(settings, db, movie):
    downloader = Mock()
    Worker(settings, db, [], downloader).start_torrent(_h("m"), None, movie, 2048)
    assert [c[0] for c in downloader.method_calls] == ["set_download_limit", "resume"]


def test_backup_start_failure_keeps_primary_downloading(settings, db, movie, monkeypatch):
    settings = _live(settings, monkeypatch)
    job_id = db.submit(movie, False)
    downloader = Mock()
    downloader.add.side_effect = [None, ProviderError(Outcome.TEMPORARY_FAILURE)]

    Worker(settings, db, [_provider("first", "second")], downloader).tick()

    job = db.job(job_id)
    assert (job["state"], job["torrent_hash"], job["backup_hash"], job["attempts"]) == (
        "DOWNLOADING", _h("first"), None, 0)
    downloader.delete_owned.assert_called_once_with(_h("second"), settings.store / "downloads")


def test_interrupted_promotion_intent_does_not_delete_current_torrent(settings, db, movie):
    job_id = _racing(db, movie)
    db.execute("INSERT INTO discard_downloads VALUES(:id,:hash)", id=job_id, hash=_h("first"))
    db.execute("INSERT INTO stalled_releases VALUES(:id,'fixture','first',:hash,0,0)", id=job_id, hash=_h("first"))
    downloader = _downloader({_h("first"): _torrent(downloaded=FAST), _h("second"): _torrent()})

    Worker(settings, db, [], downloader).tick()

    assert [c.args[0] for c in downloader.delete_owned.call_args_list] == [_h("second")]
    assert db.job(job_id)["torrent_hash"] == _h("first")
    assert not db.execute("SELECT 1 FROM stalled_releases")


def test_race_timer_starts_when_aiarr_started_the_download(settings, db, movie):
    job_id = _racing(db, movie)
    db.transition(job_id, "DOWNLOADING", download_started=time.time())
    downloader = _downloader({_h("first"): _torrent(added_on=time.time() - 9999), _h("second"): _torrent()})
    Worker(settings, db, [], downloader).tick()
    downloader.delete_owned.assert_not_called()


def test_backup_size_counts_against_free_space(settings, db, movie, monkeypatch):
    settings = _live(settings, monkeypatch)
    other = _racing(db, Work(kind="movie", external_id=2, title="Other", year=2024))
    db.execute("UPDATE jobs SET reserved=100,backup_reserved=1000000 WHERE id=:id", id=other)
    monkeypatch.setattr("mae.worker.shutil.disk_usage", lambda _p: Mock(free=10**6 + settings.reserve_bytes))
    job_id = db.submit(movie, False)
    Worker(settings, db, [_provider("first")], _downloader({_h("first"): _torrent(downloaded=1)})).tick()
    assert db.job(job_id)["reason"] == "INSUFFICIENT_SPACE"


def test_movie_release_over_file_limit_is_rejected(settings, db, movie):
    provider = _provider("big", "ok")
    provider.search.return_value[0].size = settings.policy.max_file_bytes + 1
    job_id = db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    job = db.job(job_id)
    assert json.loads(job["candidate"])["release"]["release_id"] == "ok"
    reasons = {c["release"]["release_id"]: c["reasons"] for c in json.loads(job["decision"])["candidates"]}
    assert reasons["big"] == ["FILE_TOO_LARGE"]


def test_torrent_with_oversized_video_file_is_not_started(settings, db, monkeypatch):
    settings = _live(settings, monkeypatch)
    limit = settings.policy.max_file_bytes
    monkeypatch.setattr("mae.worker.torrent_metadata", lambda raw, **_: (
        raw.decode().ljust(40, "0"), limit + 2, [("S01E01.mkv", limit + 1), ("S01E02.mkv", 1)]))
    job_id = db.submit(Work(kind="series", external_id=9, title="Example", seasons=[1],
                            episodes=["S01E01", "S01E02"]), False)
    provider = _provider("pack")
    provider.search.return_value[0].title = "Example.S01.1080p.WEB-DL.H.264"
    downloader = Mock()
    Worker(settings, db, [provider], downloader).tick()
    assert db.job(job_id)["reason"] == "FILE_SIZE_EXCEEDS_POLICY"
    downloader.add.assert_not_called()


def test_torrent_metadata_reports_file_sizes():
    import bencodepy

    from mae.downloads import torrent_metadata
    raw = bencodepy.encode({b"info": {b"name": b"Movie.mkv", b"length": 5, b"piece length": 16384,
                                      b"pieces": b"0" * 20}})
    assert torrent_metadata(raw, with_sizes=True)[2] == [("Movie.mkv", 5)]
    assert torrent_metadata(raw)[2] == ["Movie.mkv"]


def test_search_progress_is_visible_before_search_finishes(settings, db, movie):
    provider = _provider("first", "second")
    job_id = db.submit(movie)
    seen = []
    details = provider.fetch_details.return_value

    def fetch(release):
        decision = json.loads(db.job(job_id)["decision"] or "{}")
        seen.append((decision.get("partial"), [c["release"]["release_id"] for c in decision.get("candidates", [])]))
        return details

    provider.fetch_details.side_effect = fetch
    Worker(settings, db, [provider], Mock()).tick()

    assert seen[1] == (True, ["first"])
    assert "partial" not in json.loads(db.job(job_id)["decision"])



def _decision(*release_ids):
    return json.dumps({"candidates": [
        {"release": {"provider": "fixture", "release_id": rid}, "reasons": [], "unknown": []}
        for rid in release_ids
    ]})


def test_releases_with_enough_seeders_are_preferred(settings, db, movie):
    provider = _provider("few", "many")
    provider.search.return_value[0].seeders = 1
    provider.search.return_value[1].seeders = 10
    job_id = db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    assert json.loads(db.job(job_id)["candidate"])["release"]["release_id"] == "many"


def test_slow_primary_loses_to_backup_with_more_seeders(settings, db, movie):
    job_id = _racing(db, movie)
    downloader = _downloader({_h("first"): _torrent(downloaded=1000, num_seeds=1, num_complete=1),
                              _h("second"): _torrent(downloaded=1, num_seeds=9)})
    Worker(settings, db, [], downloader).tick()
    assert (db.job(job_id)["reason"], db.job(job_id)["torrent_hash"]) == ("BACKUP_PROMOTED", _h("second"))


def test_both_slow_switch_when_alternatives_exist(settings, db, movie):
    job_id = _racing(db, movie)
    db.execute("UPDATE jobs SET decision=:d WHERE id=:id", d=_decision("first", "second", "third"), id=job_id)
    downloader = _downloader({_h("first"): _torrent(downloaded=1000), _h("second"): _torrent(num_seeds=0,
                                                                                          num_complete=0)})
    Worker(settings, db, [], downloader).tick()
    assert (db.job(job_id)["state"], db.job(job_id)["reason"]) == ("RETRY_SCHEDULED", "DOWNLOAD_TOO_SLOW")


def test_slow_primary_is_kept_when_nothing_else_is_left(settings, db, movie):
    job_id = _racing(db, movie)
    db.execute("UPDATE jobs SET decision=:d WHERE id=:id", d=_decision("first", "second"), id=job_id)
    downloader = _downloader({_h("first"): _torrent(downloaded=1000), _h("second"): _torrent(num_seeds=0,
                                                                                          num_complete=0)})
    Worker(settings, db, [], downloader).tick()
    job = db.job(job_id)
    assert (job["reason"], job["torrent_hash"], job["backup_hash"]) == (
        "DOWNLOAD_SLOW_NO_ALTERNATIVE", _h("first"), None)


def _single(db, movie, decision):
    job_id = db.submit(movie)
    db.transition(job_id, "DOWNLOADING", torrent_hash=_h("first"), decision=decision,
                  candidate=json.dumps({"release": {"provider": "fixture", "release_id": "first"}}))
    return job_id


def test_download_over_eta_limit_is_replaced(settings, db, movie):
    job_id = _single(db, movie, _decision("first", "second"))
    slow = _torrent(added_on=time.time() - 3600, downloaded=10**6, amount_left=10**11, eta=10**6, progress=0.01)
    Worker(settings, db, [], _downloader({_h("first"): slow})).tick()
    assert db.job(job_id)["reason"] == "DOWNLOAD_TOO_SLOW"


def test_eta_check_keeps_download_in_several_cases(settings, db, movie):
    slow = dict(added_on=time.time() - 3600, downloaded=10**6, amount_left=10**11, eta=10**6, progress=0.01)
    cases = [
        (_decision("first"), slow),                                  # nothing else to try
        (_decision("first", "second"), {**slow, "eta": 600}),        # current rate is fine
        (_decision("first", "second"), {**slow, "progress": 0.6}),   # more than half done
        (_decision("first", "second"), {**slow, "added_on": time.time() - 200}),  # too early
    ]
    for decision, torrent in cases:
        job_id = _single(db, movie, decision)
        Worker(settings, db, [], _downloader({_h("first"): _torrent(**torrent)})).tick()
        assert db.job(job_id)["state"] == "DOWNLOADING", torrent
        for table in ("job_downloads", "events", "jobs"):
            db.execute(f"DELETE FROM {table}")



def test_moving_backup_with_one_seed_replaces_dead_primary(settings, db, movie):
    job_id = _racing(db, movie)
    downloader = _downloader({_h("first"): _torrent(), _h("second"): _torrent(downloaded=1, num_seeds=1,
                                                                             num_complete=1)})
    Worker(settings, db, [], downloader).tick()
    assert (db.job(job_id)["reason"], db.job(job_id)["torrent_hash"]) == ("BACKUP_PROMOTED", _h("second"))


def test_slow_releases_are_last_resort_and_never_forgotten(settings, db, movie):
    job_id = db.submit(movie)
    db.execute("INSERT INTO stalled_releases VALUES(:id,'fixture','first',NULL,0,1)", id=job_id)
    db.execute("INSERT INTO stalled_releases VALUES(:id,'fixture','second',NULL,0,0)", id=job_id)

    Worker(settings, db, [_provider("first", "second")], Mock()).tick()

    job = db.job(job_id)
    assert json.loads(job["candidate"])["release"]["release_id"] == "first"
    rows = db.execute("SELECT release_id,slow FROM stalled_releases WHERE job_id=:id", id=job_id)
    assert [(r["release_id"], r["slow"]) for r in rows] == [("first", 1), ("second", 0)]



def test_lower_resolution_is_chosen_only_without_preferred_one(settings, db, movie):
    provider = _provider("hd", "fullhd")
    provider.search.return_value[0].title = "Example.2024.720p.WEB-DL.H.264"
    provider.search.return_value[0].seeders = 50
    provider.search.return_value[1].seeders = 2
    job_id = db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    assert json.loads(db.job(job_id)["candidate"])["release"]["release_id"] == "fullhd"

    only_hd = _provider("hd")
    only_hd.search.return_value[0].title = "Example.2024.720p.WEB-DL.H.264"
    other = db.submit(Work(kind="movie", external_id=2, title="Example", year=2024))
    Worker(settings, db, [only_hd], Mock()).tick()
    assert json.loads(db.job(other)["candidate"])["release"]["release_id"] == "hd"


def test_lower_resolution_is_rejected_when_fallback_is_disabled(settings, db, movie):
    settings = settings.model_copy(update={"policy": settings.policy.model_copy(update={"fallback_resolutions": []})})
    provider = _provider("hd")
    provider.search.return_value[0].title = "Example.2024.720p.WEB-DL.H.264"
    job_id = db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    assert db.job(job_id)["reason"] == "NO_PROVEN_CANDIDATE"



def _res(release_id, resolution):
    return {"release": {"provider": "fixture", "release_id": release_id}, "parsed": {"screen_size": resolution},
            "reasons": [], "unknown": []}


def test_moving_1080p_is_not_replaced_by_720p(settings, db, movie):
    job_id = db.submit(movie)
    db.transition(
        job_id, "DOWNLOADING", torrent_hash=_h("first"), backup_hash=_h("second"),
        candidate=json.dumps(_res("first", "1080p")), backup_candidate=json.dumps(_res("second", "720p")),
        decision=json.dumps({"candidates": [_res("first", "1080p"), _res("second", "720p"), _res("third", "720p")]}),
    )
    downloader = _downloader({_h("first"): _torrent(downloaded=1000, num_seeds=1, num_complete=1),
                              _h("second"): _torrent(downloaded=1, num_seeds=40)})
    Worker(settings, db, [], downloader).tick()
    job = db.job(job_id)
    assert (job["reason"], job["torrent_hash"], job["backup_hash"]) == (
        "DOWNLOAD_SLOW_NO_ALTERNATIVE", _h("first"), None)


def test_dead_1080p_is_replaced_by_720p(settings, db, movie):
    job_id = db.submit(movie)
    db.transition(
        job_id, "DOWNLOADING", torrent_hash=_h("first"), backup_hash=_h("second"),
        candidate=json.dumps(_res("first", "1080p")), backup_candidate=json.dumps(_res("second", "720p")),
    )
    downloader = _downloader({_h("first"): _torrent(), _h("second"): _torrent(downloaded=1)})
    Worker(settings, db, [], downloader).tick()
    assert (db.job(job_id)["reason"], db.job(job_id)["torrent_hash"]) == ("BACKUP_PROMOTED", _h("second"))


def test_1080p_over_eta_limit_is_replaced_by_720p(settings, db, movie):
    job_id = db.submit(movie)
    db.transition(job_id, "DOWNLOADING", torrent_hash=_h("first"), candidate=json.dumps(_res("first", "1080p")),
                  decision=json.dumps({"candidates": [_res("first", "1080p"), _res("second", "720p")]}))
    slow = _torrent(added_on=time.time() - 3600, downloaded=10**6, amount_left=10**11, eta=10**6, progress=0.01)
    Worker(settings, db, [], _downloader({_h("first"): slow})).tick()
    assert db.job(job_id)["reason"] == "DOWNLOAD_TOO_SLOW"


def test_720p_backup_replaces_1080p_that_would_take_too_long(settings, db, movie):
    job_id = db.submit(movie)
    db.transition(
        job_id, "DOWNLOADING", torrent_hash=_h("first"), backup_hash=_h("second"),
        candidate=json.dumps(_res("first", "1080p")), backup_candidate=json.dumps(_res("second", "720p")),
    )
    downloader = _downloader({
        _h("first"): _torrent(downloaded=1000, num_seeds=1, num_complete=1, amount_left=10**11, eta=10**6),
        _h("second"): _torrent(downloaded=1, num_seeds=40),
    })
    Worker(settings, db, [], downloader).tick()
    assert (db.job(job_id)["reason"], db.job(job_id)["torrent_hash"]) == ("BACKUP_PROMOTED", _h("second"))


def test_deepseek_claims_and_unlabeled_codec_allow_selection(settings, db, movie, monkeypatch):
    from mae.domain import Claim

    provider = _provider("rutracker")
    provider.search.return_value[0].title = "Example.2024.1080p.WEB-DL"
    provider.fetch_details.return_value = ReleaseDetails(evidence=[
        Evidence(id="card", text="Перевод: Профессиональный (многоголосый) x2", method="fixture")
    ])
    claim = Claim(field="audio_language", value="rus", evidence_id="card",
                  quote="Перевод: Профессиональный (многоголосый) x2", method="deepseek")
    monkeypatch.setattr("mae.worker.DeepSeek.interpret", lambda *_a, **_k: Mock(claims=[claim]))
    job_id = db.submit(movie)

    Worker(settings, db, [provider], Mock()).tick()

    job = db.job(job_id)
    assert job["reason"] == "DRY_RUN_SELECTED"
    assert json.loads(job["candidate"])["release"]["release_id"] == "rutracker"


def test_release_title_alone_is_sent_to_deepseek(settings, db, movie, monkeypatch):
    provider = _provider("kinozal")
    provider.search.return_value[0].title = "Example 2024 WEBDL 1080p - RUSSIAN"
    provider.fetch_details.return_value = ReleaseDetails()
    seen = []
    monkeypatch.setattr("mae.worker.DeepSeek.interpret", lambda _self, evidence, **_k: seen.append(evidence))
    db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    assert [e.id for e in seen[0]] == ["title"]


def test_tracker_translation_marker_in_title_needs_no_deepseek(settings, db, movie, monkeypatch):
    provider = _provider("kinozal")
    provider.search.return_value[0].title = "Example (2024) WEB-DL 1080p | MVO"
    provider.fetch_details.return_value = ReleaseDetails()
    seen = []
    monkeypatch.setattr("mae.worker.DeepSeek.interpret", lambda _self, evidence, **_k: seen.append(evidence))
    job_id = db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    assert not seen
    assert db.job(job_id)["reason"] == "DRY_RUN_SELECTED"


def test_card_is_not_fetched_for_a_release_its_title_already_rules_out(settings, db, movie):
    provider = _provider("huge", "ok")
    provider.search.return_value[0].size = settings.policy.max_bytes + 1
    db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    fetched = [call.args[0].release_id for call in provider.fetch_details.call_args_list]
    assert fetched == ["ok"]


def test_cards_are_fetched_best_resolution_and_most_seeders_first(settings, db, movie):
    provider = _provider("sd", "few", "many")
    titles = {"sd": "Example.2024.720p.WEB-DL.H.264", "few": "Example.2024.1080p.WEB-DL.H.264",
              "many": "Example.2024.1080p.WEB-DL.H.264"}
    for release in provider.search.return_value:
        release.title = titles[release.release_id]
        release.seeders = 50 if release.release_id == "many" else 1
    db.submit(movie)
    Worker(settings, db, [provider], Mock()).tick()
    fetched = [call.args[0].release_id for call in provider.fetch_details.call_args_list]
    assert fetched == ["many", "few", "sd"]


def test_latin_alias_is_searched_once_and_results_are_deduplicated(settings, db):
    work = Work(kind="movie", external_id=152601, title="Она", aliases=["Her"], year=2013)
    provider = _provider("same")
    provider.search.side_effect = lambda query: [
        SearchResult(provider="fixture", release_id="same", title="Она / Her (2013) 1080p WEB-DL H.264", size=1000),
        *([SearchResult(provider="fixture", release_id="eng", title="Her.2013.1080p.WEB-DL.H.264", size=1000)]
          if query.title == "Her" else []),
    ]
    db.submit(work)
    Worker(settings, db, [provider], Mock()).tick()
    assert [call.args[0].title for call in provider.search.call_args_list] == ["Она", "Her"]
    fetched = sorted(call.args[0].release_id for call in provider.fetch_details.call_args_list)
    assert fetched == ["eng", "same"]


def test_downloads_are_checked_while_a_long_search_runs(settings, db, movie):
    downloading = _racing(db, Work(kind="movie", external_id=2, title="Other", year=2024))
    db.execute("UPDATE jobs SET backup_hash=NULL,next_run=:later WHERE id=:id", later=time.time() + 3600,
               id=downloading)
    db.submit(movie)
    downloader = _downloader({_h("first"): _torrent(progress=0.5, added_on=time.time())})
    worker = Worker(settings, db, [_provider("a", "b")], downloader)
    worker.monitor_interval = 0
    db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=downloading)
    worker.process(db.jobs()[-1])
    downloader.info.assert_any_call(_h("first"))
