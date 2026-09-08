"""Explicit opt-in real swarm test; not collected by offline pytest.

Creates original synthetic media, transfers between two isolated qBittorrent
5.2.3 processes, probes every file and checks restart-safe hardlink publication.
Does not claim Seerr/Jellyfin E2E or Russian speech identification.
"""

import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import bencodepy
import qbittorrentapi

from mae.config import Settings
from mae.downloads import Downloader, torrent_metadata
from mae.verification import probe, publish_hardlink, verify


def wait_until(predicate, message, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except qbittorrentapi.APIError:
            pass
        time.sleep(1)
    raise AssertionError(message)


def main():
    root = Path("/data/store")
    seed_dir = Path("/seed/downloads/films")
    seed_dir.mkdir(parents=True, exist_ok=True)
    for p in [root / "downloads/films", root / "films"]:
        p.mkdir(parents=True, exist_ok=True)
    media = seed_dir / "MAE-Synthetic.2024.1080p.WEB-DL.H264.mkv"
    if not media.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=1920x1080:r=10",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-t",
                "3",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
                "-metadata:s:a:0",
                "language=rus",
                str(media),
            ],
            check=True,
            timeout=40,
        )
    content = media.read_bytes()
    piece_length = 16384
    pieces = b"".join(
        hashlib.sha1(content[i : i + piece_length]).digest() for i in range(0, len(content), piece_length)
    )
    raw = bencodepy.encode(
        {
            b"info": {
                b"length": len(content),
                b"name": media.name.encode(),
                b"piece length": piece_length,
                b"pieces": pieces,
            }
        }
    )
    torrent_hash, size, _ = torrent_metadata(raw)
    seed_ip = socket.gethostbyname("seed")
    leecher_ip = socket.gethostbyname("leecher")
    seed = qbittorrentapi.Client(
        host=f"http://{seed_ip}:8080", SIMPLE_RESPONSES=True, REQUESTS_ARGS={"timeout": 3}
    )
    settings = Settings(
        api_key="isolated-test-key-000000000000", qbit_url=f"http://{leecher_ip}:8080", _env_file=None
    )
    downloader = Downloader(settings)
    wait_until(lambda: seed.app_version(), "seed unavailable")
    wait_until(lambda: downloader.client.app_version(), "leecher unavailable")
    if not seed.torrents_info(torrent_hashes=torrent_hash):
        seed.torrents_add(
            torrent_files=raw, save_path="/data/store/downloads/films", use_auto_torrent_management=False
        )
    wait_until(
        lambda: (
            seed.torrents_info(torrent_hashes=torrent_hash)
            and seed.torrents_info(torrent_hashes=torrent_hash)[0]["progress"] == 1
        ),
        "seed verification failed",
    )
    downloader.add(raw, torrent_hash, "movie")
    downloader.add(raw, torrent_hash, "movie")
    seed_ip = socket.gethostbyname("seed")
    downloader.client.torrents_add_peers(torrent_hashes=torrent_hash, peers=f"{seed_ip}:6881")
    wait_until(
        lambda: downloader.info(torrent_hash) and downloader.info(torrent_hash)["progress"] == 1,
        "swarm transfer incomplete",
    )
    downloaded = root / "downloads/films" / media.name
    assert downloaded.read_bytes() == content
    data = verify(probe(downloaded, root), settings.policy)
    target = root / "films" / "MAE Synthetic" / media.name
    publish_hardlink(downloaded, target, root)
    publish_hardlink(downloaded, target, root)
    assert os.path.samefile(downloaded, target)
    assert len(downloader.client.torrents_info(category="mae")) == 1
    print(
        json.dumps(
            {
                "result": "PASS",
                "qbit": downloader.client.app_version(),
                "bytes": size,
                "hardlink_count": target.stat().st_nlink,
                "audio_tag": data["streams"][1]["tags"]["language"],
                "scope": "real isolated swarm, probe, hardlink; not full Seerr/Jellyfin E2E",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
