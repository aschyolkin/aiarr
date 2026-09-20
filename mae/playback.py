"""Read-only Jellyfin playback evidence; never certifies the physical display."""

import hashlib
import json
from datetime import datetime, timezone

from mae.jellyfin import Jellyfin


def playback_evidence(session):
    state = session.get("PlayState") or {}
    transcode = session.get("TranscodingInfo") or {}
    method = state.get("PlayMethod")
    video_direct = transcode.get("IsVideoDirect")
    audio_direct = transcode.get("IsAudioDirect")
    # Missing telemetry is unknown, never evidence of successful Direct Play.
    if method == "DirectPlay" and transcode:
        mode = "INCONSISTENT_TELEMETRY"
    elif method == "DirectPlay":
        mode = "DIRECT_PLAY"
    elif video_direct is False:
        mode = "VIDEO_TRANSCODE"
    elif video_direct is True and audio_direct is True:
        mode = "REMUX"
    elif video_direct is True and audio_direct is False:
        mode = "AUDIO_TRANSCODE"
    elif method == "DirectStream":
        mode = "DIRECT_STREAM_DETAILS_UNKNOWN"
    else:
        mode = "UNKNOWN"
    item = session.get("NowPlayingItem") or {}
    selected_audio = state.get("AudioStreamIndex")
    streams = item.get("MediaStreams") or []
    # Do not choose an arbitrary audio track if the client did not report one.
    audio = next((s for s in streams if s.get("Type") == "Audio"
                  and selected_audio is not None and s.get("Index") == selected_audio), {})
    video = next((s for s in streams if s.get("Type") == "Video"), {})
    subtitle_index = state.get("SubtitleStreamIndex")
    return {
        "session_ref": hashlib.sha256(str(session.get("Id", "")).encode()).hexdigest()[:16],
        "client": session.get("Client"),
        "client_version": session.get("ApplicationVersion"),
        "playing": bool(item),
        "paused": state.get("IsPaused"),
        "position_ticks": state.get("PositionTicks"),
        "mode": mode if item else "IDLE",
        "reported_method": method,
        "video_passthrough": True if mode == "DIRECT_PLAY" else video_direct,
        "audio_passthrough": True if mode == "DIRECT_PLAY" else audio_direct,
        "transcode_reasons": transcode.get("TranscodeReasons", []),
        "output_container": transcode.get("Container"),
        "output_bitrate": transcode.get("Bitrate"),
        "subtitle_selected": (subtitle_index >= 0) if isinstance(subtitle_index, int) else None,
        "source_video": {k: video.get(k) for k in (
            "Codec", "Profile", "Width", "Height", "BitDepth", "VideoRangeType", "DvProfile"
        )},
        "selected_audio": {k: audio.get(k) for k in ("Codec", "Channels", "Language")},
        "visual_confirmation": "REQUIRED",
        "audible_confirmation": "REQUIRED",
    }


def snapshot(settings):
    with Jellyfin(settings).client() as client:
        response = client.get("/Sessions")
        response.raise_for_status()
        sessions = response.json()
        if not isinstance(sessions, list):
            raise ValueError("INVALID_SESSION_RESPONSE")
        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "server_telemetry_only",
            "sessions": [playback_evidence(s) for s in sessions],
        }


def main():
    from mae.config import Settings

    try:
        print(json.dumps(snapshot(Settings()), ensure_ascii=False))
    except Exception as exc:
        # HTTP exception strings can include private URLs; emit type only.
        print(json.dumps({"result": "FAIL", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
