from fastapi.testclient import TestClient

from mae.api import create_app
from mae.domain import Work


class ApprovedSeerr:
    def approved(self):
        return [{"type": "movie", "media": {"tmdbId": 1}}, {"type": "tv", "media": {"tvdbId": 2}}]

    def work(self, request):
        if request["type"] == "movie":
            return Work(kind="movie", external_id=1, title="Example", year=2024)
        return Work(
            kind="series",
            external_id=2,
            title="Example Show",
            seasons=[1, 3],
            episodes=["S01E01", "S03E01", "S03E02"],
        )


def test_movie_and_selected_season(settings, db):
    client = TestClient(create_app(settings, db, ApprovedSeerr()))
    assert client.get("/health").status_code == 401
    client.headers["X-Api-Key"] = settings.api_key.get_secret_value()
    r = "/radarr/api/v3"
    s = "/sonarr/api/v3"
    assert client.get(r + "/qualityProfile").json()[0]["id"] == 1
    assert client.get(r + "/movie/lookup?term=tmdb:1").json()[0]["title"] == "Example"
    payload = {"tmdbId": 1, "qualityProfileId": 1, "rootFolderPath": "/data/store/films"}
    first = client.post(r + "/movie", json=payload)
    assert first.status_code == 200
    assert client.post(r + "/movie", json=payload).json()["id"] == first.json()["id"]
    assert client.post(r + "/movie", json={**payload, "tmdbId": 99}).status_code == 409
    seasons = {"tvdbId": 2, "qualityProfileId": 1, "seasons": [{"seasonNumber": 3, "monitored": True}]}
    result = client.post(s + "/series", json=seasons)
    assert result.status_code == 200
    assert result.json()["seasons"][0]["seasonNumber"] == 3
    assert result.json()["statistics"]["episodeCount"] == 2
    assert len(db.jobs()) == 2
    assert (
        client.post(
            r + "/command", json={"name": "MoviesSearch", "movieIds": [first.json()["id"]]}
        ).status_code
        == 200
    )


def test_cancel_no_delete(settings, db, movie):
    job_id = db.submit(movie)
    client = TestClient(
        create_app(settings, db, ApprovedSeerr()), headers={"X-Api-Key": settings.api_key.get_secret_value()}
    )
    assert client.delete(f"/radarr/api/v3/movie/{job_id}?deleteFiles=true").status_code == 409
    assert client.post(f"/admin/jobs/{job_id}/cancel").status_code == 200
    assert db.job(job_id)["state"] == "CANCELLED"
    db.transition(job_id, "DOWNLOADING")
    assert db.job(job_id)["state"] == "CANCELLED"
