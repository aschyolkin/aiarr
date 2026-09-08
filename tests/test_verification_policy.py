import pytest

from mae.config import Policy
from mae.domain import Work
from mae.verification import verify


@pytest.mark.parametrize(
    "width,expected,reason",
    [
        (720, None, "BELOW_POLICY"),
        (1920, {"screen_size": "2160p", "video_codec": "H.264"}, "RESOLUTION_DIFFERS"),
        (1920, {"screen_size": "1080p", "video_codec": "H.265"}, "CODEC_DIFFERS"),
    ],
)
def test_probe_contradicts_release(width, expected, reason):
    probe = {
        "format": {"duration": "90"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": width, "height": 800},
            {"codec_type": "audio", "tags": {"language": "rus"}},
        ],
    }
    with pytest.raises(ValueError, match=reason):
        verify(probe, Policy(), expected)


def test_new_episode_reopens_completed_series(db):
    work = Work(kind="series", external_id=9, title="Example", seasons=[1], episodes=["S01E01"])
    job_id = db.submit(work)
    db.transition(job_id, "AVAILABLE")
    assert db.submit(work) == job_id
    assert db.job(job_id)["state"] == "AVAILABLE"
    assert db.submit(work.model_copy(update={"episodes": ["S01E01", "S01E02"]})) == job_id
    assert db.job(job_id)["state"] == "REQUESTED"
