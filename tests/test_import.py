import os
import subprocess
from unittest.mock import patch

import bencodepy
import pytest

from mae.config import Policy
from mae.downloads import safe_relative, torrent_metadata
from mae.verification import probe, publish_hardlink, verify


@pytest.mark.parametrize(
    "path", ["../evil", "/etc/passwd", "folder/../../escape", "a\\..\\b", "a/\x00b", ".hidden/file"]
)
def test_traversal(path):
    with pytest.raises(ValueError):
        safe_relative(path)


def test_torrent_parse():
    raw = bencodepy.encode(
        {b"info": {b"length": 3, b"name": b"movie.mkv", b"piece length": 16384, b"pieces": b"a" * 20}}
    )
    h, size, names = torrent_metadata(raw)
    assert len(h) == 40 and size == 3 and names == ["movie.mkv"]


def test_hardlink_restart_and_collision(tmp_path):
    source = tmp_path / "download.mkv"
    source.write_bytes(b"unchanged")
    target = tmp_path / "library" / "movie.mkv"
    publish_hardlink(source, target, tmp_path)
    publish_hardlink(source, target, tmp_path)
    assert source.stat().st_ino == target.stat().st_ino and source.stat().st_nlink == 2
    other = tmp_path / "other.mkv"
    other.write_bytes(b"old library contents")
    with pytest.raises(ValueError, match="IMPORT_COLLISION"):
        publish_hardlink(source, other, tmp_path)
    assert other.read_bytes() == b"old library contents"


def test_exdev_no_copy(tmp_path):
    source = tmp_path / "file.mkv"
    source.write_bytes(b"source")
    dest = tmp_path / "out.mkv"
    with patch("os.link", side_effect=OSError(18, "Cross-device link")):
        with pytest.raises(OSError):
            publish_hardlink(source, dest, tmp_path)
    assert not dest.exists() and source.read_bytes() == b"source"


def test_symlink(tmp_path):
    source = tmp_path / "source"
    source.symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="SYMLINK"):
        publish_hardlink(source, tmp_path / "target", tmp_path)


def test_actual_ffprobe_and_hardlink(tmp_path):
    source = tmp_path / "generated.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1920x1080:r=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-metadata:s:a:0",
            "language=rus",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    data = verify(probe(source, tmp_path), Policy())
    target = tmp_path / "library" / "generated.mkv"
    publish_hardlink(source, target, tmp_path)
    assert os.path.samefile(source, target)
    assert data["streams"][0]["width"] == 1920
    # Fixture has synthetic silence, not Russian speech. Language check only reads tags.
    for stream in data["streams"]:
        if stream["codec_type"] == "audio":
            stream["tags"]["language"] = "eng"
    with pytest.raises(ValueError, match="RUSSIAN_AUDIO"):
        verify(data, Policy())
