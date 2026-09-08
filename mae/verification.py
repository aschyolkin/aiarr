import json
import os
import stat
import subprocess


def check_path(path, root):
    if not path.is_absolute() or not path.is_relative_to(root):
        raise ValueError("PATH_OUTSIDE_STORE")
    for parent in [path, *path.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError("SYMLINK_NOT_ALLOWED")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("PATH_OUTSIDE_STORE")


def probe(path, root):
    check_path(path, root)
    if path.suffix.lower() not in {".mkv", ".mp4", ".m4v", ".avi", ".ts"}:
        raise ValueError("UNSUPPORTED_CONTAINER")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("NOT_REGULAR_FILE")
    # No shell. Protocol whitelist forbids media references making HTTP requests.
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-probesize",
            "10000000",
            "-analyzeduration",
            "10000000",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    if len(result.stdout) > 2_000_000:
        raise ValueError("PROBE_TOO_LARGE")
    return json.loads(result.stdout)


def verify(data, policy, expected=None):
    streams = data.get("streams", [])
    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos or float(data.get("format", {}).get("duration", 0)) <= 0:
        raise ValueError("MISSING_VIDEO_OR_DURATION")
    codecs = {"H.264": "h264", "H.265": "hevc"}
    if any(v.get("codec_name") not in [codecs.get(c, c.lower()) for c in policy.codecs] for v in videos):
        raise ValueError("VIDEO_CODEC_MISMATCH")
    # Cropped cinema content may have fewer vertical pixels. Do not invent resolution.
    if any(v.get("width", 0) > 4096 or v.get("height", 0) > 2160 for v in videos):
        raise ValueError("VIDEO_DIMENSIONS_UNSUPPORTED")
    for video in videos:
        width = video.get("width", 0)
        resolution = 2160 if width >= 3840 else 1080 if width >= 1920 else None
        if resolution not in policy.resolutions:
            raise ValueError("VIDEO_RESOLUTION_BELOW_POLICY")
        if expected and expected.get("screen_size") != f"{resolution}p":
            raise ValueError("VIDEO_RESOLUTION_DIFFERS_FROM_RELEASE")
        if expected and codecs.get(expected.get("video_codec")) != video.get("codec_name"):
            raise ValueError("VIDEO_CODEC_DIFFERS_FROM_RELEASE")
    if policy.russian_required and not any(
        a.get("tags", {}).get("language", "").lower() in {"rus", "ru"} for a in audio
    ):
        raise ValueError("RUSSIAN_AUDIO_NOT_TAGGED")
    return data


def publish_hardlink(source, destination, root):
    check_path(source, root)
    check_path(destination, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = source.stat()
    if src.st_dev != destination.parent.stat().st_dev:
        raise ValueError("EXDEV_DIFFERENT_DATASETS")
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError:
        target = destination.stat()
        if (target.st_dev, target.st_ino) != (src.st_dev, src.st_ino):
            raise ValueError("IMPORT_COLLISION") from None
    target = destination.stat()
    if (target.st_dev, target.st_ino) != (src.st_dev, src.st_ino) or target.st_nlink < 2:
        raise ValueError("HARDLINK_VERIFICATION_FAILED")
    directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return target
