from fastapi.testclient import TestClient

from mae.api import create_app
from tests.test_contract import ApprovedSeerr


def test_servarr_tags_are_persistent_and_validated(settings, db):
    client = TestClient(
        create_app(settings, db, ApprovedSeerr()), headers={"X-Api-Key": settings.api_key.get_secret_value()}
    )
    path = "/radarr/api/v3/tag"
    assert client.get(path).json() == []
    first = client.post(path, json={"label": "foreign"})
    assert first.status_code == 201
    tag = first.json()
    assert client.post(path, json={"label": "foreign"}).status_code == 409
    assert client.post(path, json={"label": "\n"}).status_code == 422
    assert client.put(f"{path}/{tag['id']}", json={"label": "approved"}).json()["label"] == "approved"
    movie = client.post(
        "/radarr/api/v3/movie",
        json={"tmdbId": 1, "qualityProfileId": 1, "rootFolderPath": "/data/store/films", "tags": [tag["id"]]},
    )
    assert movie.status_code == 200 and movie.json()["tags"] == [tag["id"]]
    assert client.delete(f"{path}/{tag['id']}").status_code == 409
    assert (
        client.post(
            "/radarr/api/v3/movie",
            json={"tmdbId": 1, "qualityProfileId": 1, "rootFolderPath": "/data/store/films", "tags": [999]},
        ).status_code
        == 422
    )
    assert client.get("/sonarr/api/v3/tag").json() == [{"id": tag["id"], "label": "approved"}]
