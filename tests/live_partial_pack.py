"""Opt-in isolated two-file qBittorrent swarm; download only episode two."""

import hashlib
import json
import socket
import time
from pathlib import Path

import bencodepy
import qbittorrentapi

from mae.config import Settings
from mae.downloads import Downloader, torrent_metadata


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
    root_name = f"MAE-Pack-{int(time.time())}"
    first = "Example.S01E01.mkv"
    second = "Example.S01E02.mkv"
    chunks = [b"A" * 131072, b"B" * 131072]
    seed_dir = Path("/seed/downloads/serials") / root_name
    seed_dir.mkdir(parents=True)
    for name, content in zip((first, second), chunks, strict=True):
        (seed_dir / name).write_bytes(content)
    data = b"".join(chunks)
    piece_length = 16384
    pieces = b"".join(
        hashlib.sha1(data[i : i + piece_length]).digest()
        for i in range(0, len(data), piece_length)
    )
    raw = bencodepy.encode({b"info": {
        b"name": root_name.encode(),
        b"piece length": piece_length,
        b"pieces": pieces,
        b"files": [
            {b"length": len(content), b"path": [name.encode()]}
            for name, content in zip((first, second), chunks, strict=True)
        ],
    }})
    torrent_hash, _, _ = torrent_metadata(raw)
    seed_ip = socket.gethostbyname("seed")
    leecher_ip = socket.gethostbyname("leecher")
    seed = qbittorrentapi.Client(
        host=f"http://{seed_ip}:8080", SIMPLE_RESPONSES=True, REQUESTS_ARGS={"timeout": 3}
    )
    settings = Settings(
        api_key="isolated-test-key-000000000000", qbit_url=f"http://{leecher_ip}:8080", _env_file=None
    )
    downloader = Downloader(settings)
    wait_until(lambda: seed.app_version() and downloader.client.app_version(), "qBittorrent unavailable")
    seed.torrents_add(
        torrent_files=raw, save_path="/data/store/downloads/serials", use_auto_torrent_management=False
    )
    wait_until(
        lambda: seed.torrents_info(torrent_hashes=torrent_hash)
        and seed.torrents_info(torrent_hashes=torrent_hash)[0]["progress"] == 1,
        "seed verification failed",
    )
    downloader.add(raw, torrent_hash, "series", ["S01E02"])
    priorities = {Path(row["name"]).name: row["priority"] for row in downloader.files(torrent_hash)}
    assert priorities == {first: 0, second: 1}, priorities
    downloader.client.torrents_add_peers(torrent_hashes=torrent_hash, peers=f"{seed_ip}:6881")
    wait_until(
        lambda: downloader.info(torrent_hash) and downloader.info(torrent_hash)["progress"] == 1,
        "selected episode transfer incomplete",
    )
    output = Path("/data/store/downloads/serials") / root_name
    assert (output / second).read_bytes() == chunks[1]
    assert not (output / first).exists() or (output / first).stat().st_size == 0
    print(json.dumps({"result": "PASS", "selected": second, "excluded": first,
                      "priorities": priorities}), flush=True)


if __name__ == "__main__":
    main()
