from fastapi.testclient import TestClient

from mae.api import create_app
from mae.db import Database
from mae.domain import Work


class SeasonsSeerr:
    def approved(self):
        return [{"type": "tv", "media": {"tvdbId": 8}, "season": s} for s in [1, 2]]

    def work(self, request):
        s = request["season"]
        return Work(
            kind="series",
            external_id=8,
            title="Series",
            seasons=[s],
            episodes=[f"S{s:02d}E01", f"S{s:02d}E02"],
        )


def test_separate_season_requests_stable_ids_and_observed_availability(settings, db):
    seerr = SeasonsSeerr()
    client = TestClient(
        create_app(settings, db, seerr), headers={"X-Api-Key": settings.api_key.get_secret_value()}
    )
    payload = {
        "tvdbId": 8,
        "qualityProfileId": 1,
        "seasons": [{"seasonNumber": s, "monitored": True} for s in [1, 2]],
    }
    added = client.post("/sonarr/api/v3/series", json=payload)
    assert added.status_code == 200
    job_id = added.json()["id"]
    url = f"/sonarr/api/v3/episode?seriesId={job_id}"
    rows = client.get(url).json()
    assert len(rows) == 4 and all(e["monitored"] and not e["hasFile"] for e in rows)
    ids = [e["id"] for e in rows]
    restarted = Database(settings.state_dir)
    newer = seerr.work({"season": 2}).model_copy(update={"episodes": ["S02E01", "S02E02", "S02E03"]})
    assert restarted.submit(newer) == job_id
    assert [e["id"] for e in client.get(url).json()][:4] == ids
    db.record_coverage(job_id, {"S01E01"})
    assert sum(e["hasFile"] for e in client.get(url).json()) == 1
    rendered = client.get(f"/sonarr/api/v3/series/{job_id}").json()
    assert rendered["statistics"]["episodeFileCount"] == 1
    monitored = client.put("/sonarr/api/v3/episode/monitor", json={"episodeIds": ids, "monitored": True})
    assert monitored.status_code == 200 and len(monitored.json()) == 4
    # New metadata in DB is not enough to authorize a new episode.
    new_id = client.get(url).json()[-1]["id"]
    assert (
        client.put(
            "/sonarr/api/v3/episode/monitor", json={"episodeIds": [new_id], "monitored": True}
        ).status_code
        == 409
    )
    db.record_coverage(job_id, set())
    assert not any(e["hasFile"] for e in client.get(url).json())
    unmonitored = client.put("/sonarr/api/v3/episode/monitor", json={"episodeIds": ids[:1], "monitored": False})
    assert unmonitored.status_code == 200
    assert not next(row for row in client.get(url).json() if row["id"] == ids[0])["monitored"]
    db.transition(job_id, "CANCELLED")
    assert not any(e["monitored"] for e in client.get(url).json())
    assert (
        client.put("/sonarr/api/v3/episode/monitor", json={"episodeIds": ids, "monitored": True}).status_code
        == 409
    )
