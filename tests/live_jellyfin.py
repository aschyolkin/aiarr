"""Real isolated Jellyfin discovery test using original synthetic media."""

import json
import socket
import time
from pathlib import Path

import httpx
from pydantic import SecretStr

from mae.config import Settings
from mae.domain import Work
from mae.importing import destination_for
from mae.jellyfin import Jellyfin
from mae.verification import publish_hardlink


def main():
    root = Path("/data/store")
    source = root / "downloads/films/MAE-Synthetic.2024.1080p.WEB-DL.H264.mkv"
    work = Work(kind="movie", external_id=1, title="MAE Library Probe", year=2024)
    target = destination_for(work, source, root)
    publish_hardlink(source, target, root)
    url = f"http://{socket.gethostbyname('jellyfin')}:8096"
    with httpx.Client(base_url=url, timeout=30, trust_env=False) as client:
        status = client.get("/System/Info/Public")
        status.raise_for_status()
        if not status.json()["StartupWizardCompleted"]:
            client.post(
                "/Startup/Configuration",
                json={"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"},
            ).raise_for_status()
            client.get("/Startup/User").raise_for_status()
            client.post(
                "/Startup/User", json={"Name": "mae-test", "Password": "isolated-fixture-only"}
            ).raise_for_status()
            client.post(
                "/Startup/RemoteAccess",
                json={"EnableRemoteAccess": False, "EnableAutomaticPortMapping": False},
            ).raise_for_status()
            client.post("/Startup/Complete").raise_for_status()
        auth = client.post(
            "/Users/AuthenticateByName",
            json={"Username": "mae-test", "Pw": "isolated-fixture-only"},
            headers={
                "Authorization": 'MediaBrowser Client="MAE tests", Device="isolated", DeviceId="mae-test", Version="0.1"'
            },
        )
        auth.raise_for_status()
        token = auth.json()["AccessToken"]
        client.headers["X-Emby-Token"] = token
        folders = client.get("/Library/VirtualFolders")
        folders.raise_for_status()
        if not any(folder["Name"] == "MAE Test Movies" for folder in folders.json()):
            client.post(
                "/Library/VirtualFolders",
                params={
                    "name": "MAE Test Movies",
                    "collectionType": "movies",
                    "paths": str(root / "films"),
                    "refreshLibrary": "true",
                },
                json={
                    "LibraryOptions": {
                        "EnableRealtimeMonitor": False,
                        "TypeOptions": [{"Type": "Movie", "MetadataFetchers": [], "ImageFetchers": []}],
                    }
                },
            ).raise_for_status()
        settings = Settings(
            api_key="isolated-test-key-000000000000",
            jellyfin_url=url,
            jellyfin_key=SecretStr(token),
            _env_file=None,
        )
        jellyfin = Jellyfin(settings)
        work = Work(kind="movie", external_id=1, title="MAE Library Probe", year=2024)
        jellyfin.refresh()
        for _ in range(45):
            if jellyfin.coverage(work) == {"movie"}:
                print(
                    json.dumps(
                        {
                            "result": "PASS",
                            "scope": "real isolated Jellyfin discovery by provider ID",
                            "version": status.json()["Version"],
                            "hardlink": target.stat().st_ino == source.stat().st_ino,
                        }
                    ),
                    flush=True,
                )
                return
            time.sleep(2)
        raise AssertionError("Jellyfin did not discover synthetic fixture by provider ID")


if __name__ == "__main__":
    main()
