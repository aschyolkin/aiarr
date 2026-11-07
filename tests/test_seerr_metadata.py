import httpx

from mae.domain import Work
from mae.seerr import Seerr

REQUEST = {"id": 1, "status": 2, "type": "movie", "media": {"tmdbId": 496243}}


def _settings(settings):
    return settings.model_copy(update={"seerr_key": settings.api_key})


def test_foreign_film_gets_its_english_title_as_alias(settings, respx_mock):
    settings = _settings(settings)
    route = respx_mock.get(settings.seerr_url + "/api/v1/movie/496243")
    route.side_effect = lambda request: httpx.Response(200, json={
        "title": "Parasite" if request.url.params.get("language") == "en" else "Паразиты",
        "originalTitle": "기생충", "originalLanguage": "ko", "releaseDate": "2019-05-30",
    })
    work = Seerr(settings).work(REQUEST)
    assert (work.title, work.aliases) == ("Паразиты", ["기생충", "Parasite"])


def test_english_original_needs_no_extra_request_and_metadata_is_cached(settings, respx_mock):
    settings = _settings(settings)
    route = respx_mock.get(settings.seerr_url + "/api/v1/movie/496243").mock(return_value=httpx.Response(
        200, json={"title": "Она", "originalTitle": "Her", "originalLanguage": "en", "releaseDate": "2013-12-18"}
    ))
    seerr = Seerr(settings)
    assert seerr.work(REQUEST).aliases == ["Her"]
    assert Seerr(settings).work(REQUEST).aliases == ["Her"]
    assert route.call_count == 1


def test_new_aliases_reach_an_existing_movie_job(db):
    job_id = db.submit(Work(kind="movie", external_id=5, title="Паразиты", aliases=["기생충"], year=2019))
    db.submit(Work(kind="movie", external_id=5, title="Паразиты", aliases=["기생충", "Parasite"], year=2019))
    assert Work.model_validate_json(db.job(job_id)["work"]).aliases == ["기생충", "Parasite"]
