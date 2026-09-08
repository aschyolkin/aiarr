"""Real Seerr onboarding and request contract, dry-run only; no production apps."""

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import httpx


@contextmanager
def engine_process(seerr_url, seerr_key, overrides=None):
    env = dict(
        os.environ,
        MAE_API_KEY="isolated-engine-key-000000000000",
        MAE_SEERR_URL=seerr_url,
        MAE_SEERR_KEY=seerr_key,
        MAE_DRY_RUN="true",
    )
    env.update(overrides or {})
    subprocess.run(["/app/.venv/bin/mae", "migrate"], env=env, check=True)
    process = subprocess.Popen(["/app/.venv/bin/mae", "api"], env=env)
    try:
        for _ in range(30):
            if process.poll() is not None:
                raise RuntimeError("Test engine exited during startup")
            try:
                response = httpx.get(
                    "http://127.0.0.1:8080/health", headers={"X-Api-Key": env["MAE_API_KEY"]}
                )
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Test engine startup timeout")
        yield env
    finally:
        process.terminate()
        process.wait(timeout=15)


def main(acquire_fixture=False):
    for suffix in ("films", "serials", "downloads/films", "downloads/serials"):
        (Path("/data/store") / suffix).mkdir(parents=True, exist_ok=True)
    seerr_url = f"http://{socket.gethostbyname('seerr')}:5055"
    engine_host = socket.gethostbyname(socket.gethostname())
    engine_url = f"http://{engine_host}:8080"
    with httpx.Client(base_url=seerr_url, timeout=60, trust_env=False) as seerr:
        status = seerr.get("/api/v1/status")
        status.raise_for_status()
        login = seerr.post(
            "/api/v1/auth/jellyfin",
            json={
                "username": "mae-test",
                "password": "isolated-fixture-only",
                "hostname": socket.gethostbyname("jellyfin"),
                "port": 8096,
                "useSsl": False,
                "urlBase": "",
                "serverType": 2,
                "email": "mae@example.invalid",
            },
        )
        if login.status_code == 500 and login.json().get("error") == "Jellyfin hostname already configured":
            # Repeated run after initial server setup omits hostname as required by Seerr.
            login = seerr.post(
                "/api/v1/auth/jellyfin", json={"username": "mae-test", "password": "isolated-fixture-only"}
            )
        login.raise_for_status()
        main_settings = seerr.get("/api/v1/settings/main")
        main_settings.raise_for_status()
        with ExitStack() as stack:
            fixture = None
            if acquire_fixture:
                from live_worker_e2e import acquisition_fixture

                metadata = seerr.get("/api/v1/movie/10378")
                metadata.raise_for_status()
                assert metadata.json().get("originalTitle") == "Big Buck Bunny"
                fixture = stack.enter_context(acquisition_fixture(metadata.json()["title"]))
            env = stack.enter_context(
                engine_process(
                    seerr_url, main_settings.json()["apiKey"], fixture.overrides if fixture else None
                )
            )
            jobs = exercise_requests(seerr, engine_host, engine_url, dry_run=not acquire_fixture)
            if fixture:
                fixture.finish(env, engine_url, jobs)


def exercise_requests(seerr, engine_host, engine_url, dry_run=True):
    for kind, folder in (("radarr", "films"), ("sonarr", "serials")):
        config = {
            "name": "MAE isolated",
            "hostname": engine_host,
            "port": 8080,
            "useSsl": False,
            "baseUrl": "/" + kind,
            "apiKey": "isolated-engine-key-000000000000",
            "isDefault": True,
            "is4k": False,
            "activeProfileId": 1,
            "activeProfileName": "base-1",
            "activeDirectory": f"/data/store/{folder}",
            "minimumAvailability": "released",
            "enableScan": True,
            "enableAutomaticSearch": True,
            "tags": [],
            "seriesType": "standard",
            "activeLanguageProfileId": 1,
            "enableSeasonFolders": True,
        }
        check = seerr.post(f"/api/v1/settings/{kind}/test", json=config)
        check.raise_for_status()
        assert check.json()["profiles"][0]["id"] == 1
        configured = seerr.get(f"/api/v1/settings/{kind}")
        configured.raise_for_status()
        if configured.json():
            service_id = configured.json()[0]["id"]
            seerr.put(f"/api/v1/settings/{kind}/{service_id}", json=config).raise_for_status()
        else:
            seerr.post(f"/api/v1/settings/{kind}", json=config).raise_for_status()
        print(json.dumps({"stage": "seerr_connection_test", "facade": kind, "result": "PASS"}), flush=True)
    seerr.post("/api/v1/settings/initialize").raise_for_status()
    requests = seerr.get("/api/v1/request", params={"take": 100})
    requests.raise_for_status()
    assert not requests.json()["results"], "Use a fresh isolated Seerr container (tmpfs config)"
    for kind, media_id, seasons in (("movie", 10378, None), ("tv", 1396, [2])):
        existing = [
            r for r in requests.json()["results"] if r["type"] == kind and r["media"]["tmdbId"] == media_id
        ]
        if not existing:
            payload = {"mediaType": kind, "mediaId": media_id, "is4k": False}
            if seasons:
                payload["seasons"] = seasons
            response = seerr.post("/api/v1/request", json=payload)
            response.raise_for_status()
            print(
                json.dumps({"stage": "request", "kind": kind, "http_status": response.status_code}),
                flush=True,
            )
    with httpx.Client(
        base_url=engine_url,
        timeout=15,
        trust_env=False,
        headers={"X-Api-Key": "isolated-engine-key-000000000000"},
    ) as engine:
        for _ in range(30):
            result = engine.get("/admin/jobs")
            result.raise_for_status()
            jobs = result.json()
            if len(jobs) >= 2:
                works = [json.loads(j["work"]) for j in jobs]
                movie = next(w for w in works if w["kind"] == "movie")
                series = next(w for w in works if w["kind"] == "series")
                assert movie["external_id"] == 10378
                assert "Big Buck Bunny" in [movie["title"], *movie["aliases"]]
                assert series["seasons"] == [2] and series["episodes"]
                assert all(ep.startswith("S02E") for ep in series["episodes"])
                assert all(bool(j["dry_run"]) == dry_run and not j["torrent_hash"] for j in jobs)
                original_ids = {j["id"] for j in jobs}
                for _ in range(2):
                    engine.post("/events/seerr", json={}).raise_for_status()
                repeated = engine.get("/admin/jobs")
                repeated.raise_for_status()
                assert len(repeated.json()) == 2
                assert {j["id"] for j in repeated.json()} == original_ids
                print(
                    json.dumps(
                        {
                            "result": "PASS",
                            "scope": "real Seerr movie and selected-season facade; worker not started yet",
                            "jobs": len(jobs),
                            "episodes": len(series["episodes"]),
                        }
                    ),
                    flush=True,
                )
                return jobs
            time.sleep(2)
    raise AssertionError("Seerr did not deliver both requests to engine")


if __name__ == "__main__":
    main(acquire_fixture="--acquire-fixture" in sys.argv)
