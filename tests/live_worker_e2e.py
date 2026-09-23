"""Opt-in synthetic acquisition through the real worker, not a tracker live test.

Seerr/TMDB identity is Big Buck Bunny; bytes are generated test patterns and
silence tagged rus, NOT that movie or Russian speech. Only the isolated fixture
provider serves those bytes. Never run this against production infrastructure.
Run live_qbit.py and live_jellyfin.py first to initialize the isolated library.
"""

import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import bencodepy
import httpx
import qbittorrentapi
from live_qbit import wait_until

from mae.downloads import torrent_metadata


class Fixture:
    def __init__(self, raw, content, filename, jellyfin_url, jellyfin_key, kind="movie"):
        self.raw = raw
        self.content = content
        self.filename = filename
        self.kind = kind
        self.expected_files = 13 if kind == "series" else 1
        self.hash, _, _ = torrent_metadata(raw)
        self.seed_ip = socket.gethostbyname("seed")
        self.leecher = qbittorrentapi.Client(
            host=f"http://{socket.gethostbyname('leecher')}:8080",
            SIMPLE_RESPONSES=True,
            REQUESTS_ARGS={"timeout": 5},
        )
        self.overrides = {
            "MAE_DRY_RUN": "false",
            "MAE_QBIT_URL": f"http://{socket.gethostbyname('leecher')}:8080",
            "MAE_JELLYFIN_URL": jellyfin_url,
            "MAE_JELLYFIN_KEY": jellyfin_key,
            "MAE_PROVIDERS": json.dumps(
                [
                    {
                        "name": "synthetic-fixture",
                        "protocol": "json",
                        "url": "http://127.0.0.1:8765/api",
                        "trusted_gateway": True,
                        "interval": 0,
                    }
                ]
            ),
        }
        self.searches = 0
        self.resolutions = 0

    def finish(self, env, engine_url, jobs):
        movie = next(j for j in jobs if json.loads(j["work"])["kind"] == self.kind)
        assert not self.leecher.torrents_info(torrent_hashes=self.hash), "Torrent must be new"
        folder = "serials" if self.kind == "series" else "films"
        assert not (Path("/data/store/downloads") / folder / self.filename).exists(), "Use fresh fixture media"
        worker = subprocess.Popen(["/app/.venv/bin/mae", "worker"], env=env)
        restarted = False
        states = []
        try:
            with httpx.Client(
                base_url=engine_url, timeout=20, trust_env=False, headers={"X-Api-Key": env["MAE_API_KEY"]}
            ) as api:
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    response = api.get(f"/admin/jobs/{movie['id']}")
                    response.raise_for_status()
                    detail = response.json()
                    job = detail["job"]
                    if not states or states[-1] != job["state"]:
                        states.append(job["state"])
                        print(json.dumps({"stage": "worker_e2e", "state": job["state"]}), flush=True)
                    assert worker.poll() is None, "Worker exited unexpectedly"
                    if job["state"] in {"NEEDS_REVIEW", "FAILED", "AUTH_REQUIRED", "WAITING_FOR_RELEASE"}:
                        raise AssertionError(json.dumps({"state": job["state"], "events": detail["events"]}))
                    if job["torrent_hash"] and not restarted:
                        # Actual process crash after the worker has added the torrent.
                        if not self.leecher.torrents_info(torrent_hashes=self.hash):
                            time.sleep(1)
                            continue
                        worker.kill()
                        worker.wait(timeout=15)
                        self.leecher.torrents_add_peers(
                            torrent_hashes=self.hash, peers=f"{self.seed_ip}:6881"
                        )
                        worker = subprocess.Popen(["/app/.venv/bin/mae", "worker"], env=env)
                        restarted = True
                    if job["state"] == "AVAILABLE":
                        assert restarted and job["torrent_hash"] == self.hash
                        assert len(detail["imports"]) == self.expected_files
                        for imported in detail["imports"]:
                            assert imported["state"] == "PUBLISHED"
                            source, destination = Path(imported["source"]), Path(imported["destination"])
                            assert source.read_bytes() == self.content
                            assert os.path.samefile(source, destination) and source.stat().st_nlink == 2
                        if self.kind == "series":
                            assert {i["episode"] for i in detail["imports"]} == {
                                f"S02E{n:02d}" for n in range(1, 14)
                            }
                        assert self.searches >= 1 and self.resolutions == 1
                        for _ in range(2):
                            api.post("/events/seerr", json={}).raise_for_status()
                        assert len(api.get("/admin/jobs").json()) == 2
                        assert len(self.leecher.torrents_info(torrent_hashes=self.hash)) == 1
                        path = (
                            f"/sonarr/api/v3/episode?seriesId={movie['id']}"
                            if self.kind == "series" else f"/radarr/api/v3/movie/{movie['id']}"
                        )
                        rendered = api.get(path)
                        rendered.raise_for_status()
                        if self.kind == "series":
                            assert len(rendered.json()) == 13
                            assert all(e["hasFile"] for e in rendered.json())
                        else:
                            assert rendered.json()["hasFile"] is True
                        print(
                            json.dumps(
                                {
                                    "result": "PASS",
                                    "scope": f"synthetic Seerr {self.kind} -> JSON provider -> worker -> qBittorrent -> probe -> hardlink -> Jellyfin",
                                    "worker_sigkill_recovery": True,
                                    "bytes": len(self.content) * self.expected_files,
                                    "imports": self.expected_files,
                                    "states": states,
                                    "limitations": "synthetic identity/audio tags; no tracker or TV playback certification",
                                }
                            ),
                            flush=True,
                        )
                        return
                    time.sleep(1)
                raise AssertionError(f"Worker acquisition timeout; states={states}")
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=15)

    def unmonitor_inflight(self, env, engine_url, jobs):
        """Exercise the real Seerr facade, worker and isolated qBittorrent pause."""
        series = next(j for j in jobs if json.loads(j["work"])["kind"] == "series")
        assert not self.leecher.torrents_info(torrent_hashes=self.hash), "Torrent must be new"
        worker = subprocess.Popen(["/app/.venv/bin/mae", "worker"], env=env)
        try:
            with httpx.Client(
                base_url=engine_url, timeout=20, trust_env=False,
                headers={"X-Api-Key": env["MAE_API_KEY"]},
            ) as api:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    assert worker.poll() is None, "Worker exited unexpectedly"
                    job = api.get(f"/admin/jobs/{series['id']}").json()["job"]
                    if job["torrent_hash"] == self.hash and self.leecher.torrents_info(torrent_hashes=self.hash):
                        break
                    if job["state"] in {"FAILED", "NEEDS_REVIEW", "AUTH_REQUIRED"}:
                        raise AssertionError(f"Acquisition stopped before unmonitor: {job['state']}")
                    time.sleep(1)
                else:
                    raise AssertionError("Torrent add timeout before unmonitor")
                episodes = api.get(f"/sonarr/api/v3/episode?seriesId={series['id']}")
                episodes.raise_for_status()
                assert len(episodes.json()) == 13
                changed = api.put(
                    "/sonarr/api/v3/episode/monitor",
                    json={"episodeIds": [episodes.json()[0]["id"]], "monitored": False},
                )
                changed.raise_for_status()
                assert changed.json()[0]["monitored"] is False
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    job = api.get(f"/admin/jobs/{series['id']}").json()["job"]
                    torrents = self.leecher.torrents_info(torrent_hashes=self.hash)
                    if (job["cancel_pause_done"] and torrents and
                            torrents[0]["state"] in {"stoppedDL", "stoppedUP", "pausedDL", "pausedUP"}):
                        assert job["state"] == "NEEDS_REVIEW"
                        assert job["reason"] == "EPISODE_MONITORING_CHANGED"
                        assert not api.get(f"/admin/jobs/{series['id']}").json()["imports"]
                        print(json.dumps({
                            "result": "PASS",
                            "scope": "isolated Seerr episode unmonitor -> worker -> qBittorrent pause",
                            "torrent_state": torrents[0]["state"],
                        }), flush=True)
                        return
                    time.sleep(1)
                raise AssertionError("Owned torrent was not paused after unmonitor")
        finally:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=15)


@contextmanager
def acquisition_fixture(localized_title, kind="movie"):
    folder = "serials" if kind == "series" else "films"
    seed_dir = Path("/seed/downloads") / folder
    seed_dir.mkdir(parents=True, exist_ok=True)
    filename = "Breaking.Bad.S02.1080p.WEB-DL.H264" if kind == "series" else "Big.Buck.Bunny.2008.1080p.WEB-DL.H264.mkv"
    media = seed_dir / (filename + ".synthetic-source.mkv" if kind == "series" else filename)
    if media.exists():
        raise AssertionError("Use a fresh E2E staging directory; never overwrite a seeded file")
    subprocess.run(
        [
            "ffmpeg",
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
    torrent_content = content
    info = {b"length": len(content), b"name": filename.encode()}
    if kind == "series":
        pack = seed_dir / filename
        pack.mkdir()  # Refuse to overwrite a previous fixture or seeded directory.
        files = []
        for number in range(1, 14):
            name = f"Breaking.Bad.S02E{number:02d}.1080p.WEB-DL.H264.mkv"
            (pack / name).write_bytes(content)
            files.append({b"length": len(content), b"path": [name.encode()]})
        info = {b"name": filename.encode(), b"files": files}
        torrent_content = content * 13
    raw = bencodepy.encode(
        {
            b"info": {
                **info,
                b"piece length": 16384,
                b"pieces": b"".join(
                    hashlib.sha1(torrent_content[i : i + 16384]).digest()
                    for i in range(0, len(torrent_content), 16384)
                ),
            }
        }
    )
    jellyfin_url = f"http://{socket.gethostbyname('jellyfin')}:8096"
    with httpx.Client(base_url=jellyfin_url, timeout=30, trust_env=False) as jellyfin:
        login = jellyfin.post(
            "/Users/AuthenticateByName",
            json={"Username": "mae-test", "Pw": "isolated-fixture-only"},
            headers={
                "Authorization": 'MediaBrowser Client="MAE E2E", Device="isolated", DeviceId="mae-e2e", Version="0.1"'
            },
        )
        login.raise_for_status()
        fixture = Fixture(raw, content, filename, jellyfin_url, login.json()["AccessToken"], kind)
        if kind == "series":
            jellyfin.headers["X-Emby-Token"] = login.json()["AccessToken"]
            jellyfin.post('/Library/VirtualFolders', params={
                'name':'MAE Test Series', 'collectionType':'tvshows',
                'paths':'/data/store/serials', 'refreshLibrary':'true',
            }, json={'LibraryOptions':{'EnableRealtimeMonitor':False,'TypeOptions':[
                {'Type':t,'MetadataFetchers':[],'ImageFetchers':[]} for t in ['Series','Season','Episode']
            ]}}).raise_for_status()
    seed = qbittorrentapi.Client(
        host=f"http://{fixture.seed_ip}:8080", SIMPLE_RESPONSES=True, REQUESTS_ARGS={"timeout": 5}
    )
    seed.torrents_add(
        torrent_files=raw, save_path="/data/store/downloads/" + folder, use_auto_torrent_management=False
    )
    wait_until(
        lambda: (
            seed.torrents_info(torrent_hashes=fixture.hash)
            and seed.torrents_info(torrent_hashes=fixture.hash)[0]["progress"] == 1
        ),
        "Synthetic seed did not verify",
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            request = urlsplit(self.path)
            params = parse_qs(request.query)
            if request.path == "/fixture.torrent":
                fixture.resolutions += 1
                body, kind = raw, "application/x-bittorrent"
            elif params.get("operation") == ["health"]:
                body, kind = b'{"status":"OK"}', "application/json"
            elif params.get("operation") == ["search"]:
                fixture.searches += 1
                # Synthetic fixture endpoint, not a guessed external tracker API.
                query = params.get("q", [""])[0].casefold()
                rows = (
                    [
                        {
                            "release": {
                                "release_id": "synthetic-" + fixture.kind + "-1",
                                "title": filename,
                                "size": len(torrent_content),
                                "seeders": 1,
                            },
                            "description": "Audio: Russian, AAC stereo.\nSynthetic silence with rus tag; no speech recognition.",
                            "download": "http://127.0.0.1:8765/fixture.torrent",
                        }
                    ]
                    if query == localized_title.casefold()
                    else []
                )
                body, kind = json.dumps({"results": rows}).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
