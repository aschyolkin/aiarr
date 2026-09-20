import json

import pytest

from mae.playback import playback_evidence, snapshot


@pytest.mark.parametrize("method,transcode,mode", [
    ("DirectPlay", {}, "DIRECT_PLAY"),
    ("Transcode", {"IsVideoDirect": True, "IsAudioDirect": True}, "REMUX"),
    ("Transcode", {"IsVideoDirect": True, "IsAudioDirect": False}, "AUDIO_TRANSCODE"),
    ("Transcode", {"IsVideoDirect": False}, "VIDEO_TRANSCODE"),
    ("DirectStream", {}, "DIRECT_STREAM_DETAILS_UNKNOWN"),
    ("Transcode", {}, "UNKNOWN"),
    (None, {}, "UNKNOWN"),
    ("DirectPlay", {"IsVideoDirect": False}, "INCONSISTENT_TELEMETRY"),
])
def test_modes_do_not_infer_tv_success(method, transcode, mode):
    result = playback_evidence({"NowPlayingItem": {"Id": "one"},
                               "PlayState": {"PlayMethod": method}, "TranscodingInfo": transcode})
    assert result["mode"] == mode
    assert result["visual_confirmation"] == result["audible_confirmation"] == "REQUIRED"


def test_unknown_audio_and_subtitle_are_not_assumed():
    session = {"NowPlayingItem": {"MediaStreams": [
        {"Type": "Audio", "Index": 1, "Language": "rus", "Codec": "aac"},
        {"Type": "Audio", "Index": 2, "Language": "eng", "Codec": "ac3"},
    ]}}
    assert playback_evidence(session)["selected_audio"]["Language"] is None
    assert playback_evidence(session)["subtitle_selected"] is None
    session["PlayState"] = {"AudioStreamIndex": 2, "SubtitleStreamIndex": -1}
    result = playback_evidence(session)
    assert result["selected_audio"]["Language"] == "eng"
    assert result["subtitle_selected"] is False


def test_snapshot_is_read_only_and_does_not_expose_private_session_fields(settings, respx_mock):
    settings = settings.model_copy(update={"jellyfin_key": settings.api_key})
    route = respx_mock.get(settings.jellyfin_url + "/Sessions").respond(200, json=[{
        "Id": "private-session", "UserName": "private-user", "RemoteEndPoint": "private-address",
        "DeviceId": "private-device", "NowPlayingItem": {"Name": "private-title", "Path": "/private/path"},
    }])
    result = snapshot(settings)
    assert result["scope"] == "server_telemetry_only"
    assert "private" not in json.dumps(result)
    assert route.call_count == 1
    assert all(call.request.method == "GET" for call in respx_mock.calls)


def test_bad_response_is_not_empty_success(settings, respx_mock):
    settings = settings.model_copy(update={"jellyfin_key": settings.api_key})
    respx_mock.get(settings.jellyfin_url + "/Sessions").respond(200, json={})
    with pytest.raises(ValueError, match="INVALID_SESSION_RESPONSE"):
        snapshot(settings)
