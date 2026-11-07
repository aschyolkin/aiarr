from unittest.mock import Mock

import httpx

from mae.errors import failure
from mae.worker import Worker


def _status(url, code):
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code, request=request))


def test_failure_names_service_and_status(settings):
    assert failure(_status(settings.jellyfin_url + "/Items?x=1", 401), settings) == ("JELLYFIN_HTTP_401", True)
    assert failure(_status(settings.seerr_url + "/api/v1/movie/1", 500), settings) == ("SEERR_HTTP_500", True)
    assert failure(
        httpx.ConnectError("x", request=httpx.Request("GET", settings.qbit_url + "/api")), settings
    ) == ("QBITTORRENT_UNREACHABLE", True)
    assert failure(
        httpx.ReadTimeout("x", request=httpx.Request("GET", settings.jellyfin_url)), settings
    ) == ("JELLYFIN_TIMEOUT", True)


def test_unknown_hosts_and_plain_errors_are_not_dependencies(settings):
    assert failure(_status("https://elsewhere.test/a", 503), settings) == ("UPSTREAM_HTTP_503", False)
    assert failure(KeyError("x"), settings) == ("KeyError", False)


def test_dependency_outage_does_not_consume_attempts(settings, db, movie, monkeypatch):
    def down(_self, _work):
        raise _status(settings.jellyfin_url + "/Items", 401)

    monkeypatch.setattr("mae.worker.Jellyfin.coverage", down)
    settings = settings.model_copy(update={"dry_run": False, "max_attempts": 1})
    job_id = db.submit(movie, False)
    for _ in range(3):
        db.execute("UPDATE jobs SET next_run=0 WHERE id=:id", id=job_id)
        Worker(settings, db, [Mock()], Mock()).tick()
    job = db.job(job_id)
    assert (job["state"], job["reason"], job["attempts"]) == ("RETRY_SCHEDULED", "JELLYFIN_HTTP_401", 0)


def test_job_error_still_consumes_attempts(settings, db, movie, monkeypatch):
    def broken(_self, _work):
        raise _status("https://elsewhere.test/a", 500)

    monkeypatch.setattr("mae.worker.Jellyfin.coverage", broken)
    settings = settings.model_copy(update={"dry_run": False, "max_attempts": 1})
    job_id = db.submit(movie, False)
    Worker(settings, db, [Mock()], Mock()).tick()
    assert (db.job(job_id)["state"], db.job(job_id)["reason"]) == ("FAILED", "UPSTREAM_HTTP_500")
