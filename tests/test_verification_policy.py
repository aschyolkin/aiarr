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


@pytest.mark.parametrize("width,height,expected_resolution", [
    (1918, 802, "1080p"),
    (3840, 1600, "2160p"),
])
def test_cinematic_crop_preserves_resolution_class(width, height, expected_resolution):
    data = {"format": {"duration": "3600"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": width, "height": height},
        {"codec_type": "audio", "tags": {"language": "rus"}},
    ]}
    assert verify(data, Policy(), {"screen_size": expected_resolution, "video_codec": "H.264"}) == data


def test_excessive_crop_is_still_below_policy():
    data = {"format": {"duration": "3600"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1918, "height": 600},
        {"codec_type": "audio", "tags": {"language": "rus"}},
    ]}
    with pytest.raises(ValueError, match="VIDEO_RESOLUTION_BELOW_POLICY"):
        verify(data, Policy(fallback_resolutions=[]), {"screen_size": "1080p", "video_codec": "H.264"})
    with pytest.raises(ValueError, match="VIDEO_RESOLUTION_DIFFERS_FROM_RELEASE"):
        verify(data, Policy(), {"screen_size": "1080p", "video_codec": "H.264"})


def test_fallback_720p_file_is_accepted_only_when_allowed():
    data = {"format": {"duration": "3600"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 536},
        {"codec_type": "audio", "tags": {"language": "rus"}},
    ]}
    verify(data, Policy(), {"screen_size": "720p", "video_codec": "H.264"})
    with pytest.raises(ValueError, match="VIDEO_RESOLUTION_BELOW_POLICY"):
        verify(data, Policy(fallback_resolutions=[]), {"screen_size": "720p", "video_codec": "H.264"})


def test_new_episode_reopens_completed_series(db):
    work = Work(kind="series", external_id=9, title="Example", seasons=[1], episodes=["S01E01"])
    job_id = db.submit(work)
    db.transition(job_id, "AVAILABLE", torrent_hash="a" * 40, candidate='{"old":true}', reserved=42)
    assert db.submit(work) == job_id
    assert db.job(job_id)["state"] == "AVAILABLE"
    assert db.submit(work.model_copy(update={"episodes": ["S01E01", "S01E02"]})) == job_id
    assert db.job(job_id)["state"] == "REQUESTED"
    assert db.job(job_id)["torrent_hash"] is None
    assert db.job(job_id)["candidate"] is None
    assert db.job(job_id)["reserved"] == 0
