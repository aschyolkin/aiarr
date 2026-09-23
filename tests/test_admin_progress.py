from fastapi.testclient import TestClient

from mae.api import create_app
from mae.domain import Work


def test_order_detail_has_episode_coverage_and_ordered_events(settings, db):
    work = Work(kind="series", external_id=22, title="Example", seasons=[3, 4],
                episodes=["S03E10", "S04E01"])
    job_id = db.submit(work, dry_run=False)
    db.record_coverage(job_id, {"S03E10"})
    db.transition(job_id, "WAITING_FOR_RELEASE", "PARTIAL_SEASON")
    client = TestClient(create_app(settings, db), headers={"X-Api-Key": settings.api_key.get_secret_value()})
    result = client.get(f"/admin/jobs/{job_id}")
    assert result.status_code == 200
    data = result.json()
    assert [(row["episode"], row["available"]) for row in data["episodes"]] == [
        ("S03E10", 1), ("S04E01", 0)
    ]
    assert data["events"][-1]["reason"] == "PARTIAL_SEASON"
    assert data["download"] is None


def test_order_detail_reports_live_torrent_progress(settings, db, movie, monkeypatch):
    job_id = db.submit(movie, dry_run=False)
    db.transition(job_id, "DOWNLOADING", torrent_hash="a" * 40)

    class FakeDownloader:
        def __init__(self, _settings):
            pass

        def info(self, _hash):
            return {"progress": 0.375, "state": "downloading", "dlspeed": 1048576,
                    "eta": 180, "amount_left": 1250000, "passkey": "must-not-leak"}

    monkeypatch.setattr("mae.downloads.Downloader", FakeDownloader)
    client = TestClient(create_app(settings, db), headers={"X-Api-Key": settings.api_key.get_secret_value()})
    data = client.get(f"/admin/jobs/{job_id}").json()
    assert data["download"] == {"progress": 0.375, "state": "downloading",
                                "download_speed": 1048576, "eta": 180, "remaining_bytes": 1250000}
    assert "must-not-leak" not in str(data)
