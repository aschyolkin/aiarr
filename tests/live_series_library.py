"""Real isolated Jellyfin episode coverage using synthetic files; no TMDB calls."""

import json
import socket
import time
from pathlib import Path

import httpx

from mae.config import Settings
from mae.domain import Work
from mae.importing import destination_for
from mae.jellyfin import Jellyfin
from mae.verification import publish_hardlink


def wait_coverage(jellyfin, work, expected):
    jellyfin.refresh()
    observed = set()
    for _ in range(45):
        observed = jellyfin.coverage(work)
        if observed == expected:
            return
        time.sleep(2)
    raise AssertionError(f"Episode coverage mismatch: expected={sorted(expected)}, actual={sorted(observed)}")


def main():
    root = Path("/data/store")
    source = root / "downloads/films/MAE-Synthetic.2024.1080p.WEB-DL.H264.mkv"
    (root / "serials").mkdir(exist_ok=True)
    url = f"http://{socket.gethostbyname('jellyfin')}:8096"
    with httpx.Client(base_url=url, timeout=30, trust_env=False) as client:
        auth = client.post(
            "/Users/AuthenticateByName",
            json={"Username": "mae-test", "Pw": "isolated-fixture-only"},
            headers={
                "Authorization": 'MediaBrowser Client="MAE series test", Device="isolated", DeviceId="mae-series", Version="0.1"'
            },
        )
        auth.raise_for_status()
        key = auth.json()["AccessToken"]
        client.headers["X-Emby-Token"] = key
        folders = client.get("/Library/VirtualFolders")
        folders.raise_for_status()
        assert not any(f["Name"] == "MAE Test Shows" for f in folders.json()), "Use fresh test library"
        client.post(
            "/Library/VirtualFolders",
            params={
                "name": "MAE Test Shows",
                "collectionType": "tvshows",
                "paths": str(root / "serials"),
                "refreshLibrary": "true",
            },
            json={
                "LibraryOptions": {
                    "EnableRealtimeMonitor": False,
                    "TypeOptions": [
                        {"Type": kind, "MetadataFetchers": [], "ImageFetchers": []}
                        for kind in ["Series", "Season", "Episode"]
                    ],
                }
            },
        ).raise_for_status()
        settings = Settings(
            api_key="isolated-series-key-000000000000", jellyfin_url=url, jellyfin_key=key, _env_file=None
        )
        work = Work(
            kind="series",
            external_id=8,
            title="MAE Synthetic Series",
            year=2024,
            seasons=[1],
            episodes=["S01E01", "S01E02", "S01E03"],
        )
        jellyfin = Jellyfin(settings)
        combined = destination_for(work, source, root, "S01E01+S01E02")
        third = destination_for(work, source, root, "S01E03")
        assert not combined.exists() and not third.exists(), "Use fresh synthetic series directory"
        publish_hardlink(source, combined, root)
        wait_coverage(jellyfin, work, {"S01E01", "S01E02"})
        publish_hardlink(source, third, root)
        wait_coverage(jellyfin, work, set(work.episodes))
        assert source.stat().st_ino == combined.stat().st_ino == third.stat().st_ino
        print(
            json.dumps(
                {
                    "result": "PASS",
                    "scope": "real Jellyfin partial season, combined episodes, later episode discovery",
                    "before": ["S01E01", "S01E02"],
                    "after": work.episodes,
                    "limitations": "synthetic direct hardlink publication; not Seerr/worker/download E2E or speech recognition",
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
